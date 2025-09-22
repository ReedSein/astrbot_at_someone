import re
import random
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
# 关键：导入消息组件模块
import astrbot.api.message_components as Comp
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.star.star_handler import star_handlers_registry

@register(
    "at_someone",          # 插件名称
    "sasapp77",            # 插件作者
    "让bot学会主动@别人，需要配合系统提示词",  # 插件描述
    "1.0.0",               # 插件版本
    ""                     # 插件仓库地址
)
class AtSomeonePlugin(Star):
    def __init__(self, context: Context, config):
        self.config = config
        self.at_pattern = re.compile(r"<@(.*?)>")
        super().__init__(context)
        star_handlers_registry._print_handlers()

    @filter.on_decorating_result()
    async def handle_add_flag(self, event: AstrMessageEvent):
        result = event.get_result()
        msg_chain = result.chain
        new_chain: list[BaseMessageComponent] = []

        # --- 私聊处理 ---
        # 私聊没有@功能，遍历并移除所有At元素
        if event.is_private_chat():
            for component in msg_chain:
                if component.type == 'Plain':
                    # 使用正则表达式替换掉所有匹配的<At>标签
                    cleaned_text = self.at_pattern.sub("", component.text)
                    if cleaned_text:  # 确保文本不为空
                        new_chain.append(Comp.Plain(text=cleaned_text))
                else:
                    new_chain.append(component)
            event.message_obj.message = new_chain
            return

        # --- 群聊处理 ---
        group = await event.get_group()
        # 创建一个昵称到ID的映射，方便快速查找
        # 注意：如果群成员信息未加载或为空，则此字典为空
        members_map = {member.nickname: member.user_id for member in group.members} if group and group.members else {}

        for component in msg_chain:
            if component.type != 'Plain':
                new_chain.append(component)
                continue

            # 处理Plain组件
            text = component.text
            last_end = 0
            
            # 使用finditer遍历所有匹配项
            for match in self.at_pattern.finditer(text):
                # 1. 添加<At>标签前的文本
                start, end = match.span()
                if start > last_end:
                    new_chain.append(Comp.Plain(text=text[last_end:start]))

                # 2. 解析<At>标签内的内容
                content = match.group(1).strip()
                user_id_to_at = None

                # 逻辑分支：ID -> 昵称 -> 尝试将昵称转为ID
                if content.isdigit():
                    # 优先处理纯数字ID
                    user_id_to_at = int(content)
                elif content in members_map:
                    # 如果是昵称，并且在群成员映射中找到了
                    user_id_to_at = int(members_map[content])
                else:
                    # 最后的尝试：昵称找不到，但它本身是否可以被视为一个ID？
                    try:
                        user_id_to_at = int(content)
                        # 如果一个非数字字符串（如"123a"）无法转换，会触发ValueError
                    except ValueError:
                        # 无法解析，记录警告并跳过
                        logger.warning(f"在群 '{group.group_id}' 中无法找到昵称为 '{content}' 的用户，且该内容无法解析为用户ID，已跳过@。")
                
                # 3. 如果成功解析出ID，则添加At组件
                if user_id_to_at is not None:
                    new_chain.append(Comp.At(qq=user_id_to_at))
                
                last_end = end

            # 4. 添加最后一个<At>标签后的剩余文本
            if last_end < len(text):
                new_chain.append(Comp.Plain(text=text[last_end:]))

        # 用构建好的新消息链替换旧的
        result.chain = new_chain