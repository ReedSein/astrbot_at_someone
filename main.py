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

    @filter.on_decorating_result(priority=-200)
    async def handle_add_flag(self, event: AstrMessageEvent):
        result = event.get_result()
        msg_chain = result.chain
        new_chain: list[BaseMessageComponent] = []

        # 私聊没有@功能，遍历并移除所有At元素
        if event.is_private_chat():
            for component in msg_chain:
                if component.type == 'Plain':
                    cleaned_text = self.at_pattern.sub("", component.text)
                    if cleaned_text:
                        new_chain.append(Comp.Plain(text=cleaned_text))
                else:
                    new_chain.append(component)
            event.message_obj.message = new_chain
            return

        group = await event.get_group()
        members_map = {member.nickname: member.user_id for member in group.members} if group and group.members else {}

        for component in msg_chain:
            if component.type != 'Plain':
                new_chain.append(component)
                continue

            text = component.text
            last_end = 0
            # 标记下一个文本片段是否需要前置零宽空格
            need_prefix_zwsp = False
            
            for match in self.at_pattern.finditer(text):
                start, end = match.span()
                if start > last_end:
                    # 添加匹配前的文本
                    prefix_text = text[last_end:start]
                    # 如果之前有At组件，需要在此文本前加零宽空格
                    if need_prefix_zwsp:
                        prefix_text = '\u200B' + prefix_text
                        need_prefix_zwsp = False
                    new_chain.append(Comp.Plain(text=prefix_text))

                content = match.group(1).strip()
                user_id_to_at = None

                if content.isdigit():
                    user_id_to_at = int(content)
                elif content in members_map:
                    user_id_to_at = int(members_map[content])
                else:
                    try:
                        user_id_to_at = int(content)
                    except ValueError:
                        logger.warning(f"在群 '{group.group_id}' 中无法找到昵称为 '{content}' 的用户，且该内容无法解析为用户ID，已跳过@。")
                
                if user_id_to_at is not None:
                    new_chain.append(Comp.At(qq=user_id_to_at))
                    # 设置标志，下一个文本需要前置零宽空格
                    need_prefix_zwsp = True
                else:
                    # 当无法解析为有效的@组件时，将原始文本发回，使失败变得可见
                    suffix_text = match.group(0)
                    if need_prefix_zwsp:
                        suffix_text = '\u200B' + suffix_text
                        need_prefix_zwsp = False
                    new_chain.append(Comp.Plain(text=suffix_text))
                
                last_end = end

            if last_end < len(text):
                # 添加剩余文本
                remaining_text = text[last_end:]
                if need_prefix_zwsp:
                    remaining_text = '\u200B' + remaining_text
                    need_prefix_zwsp = False
                new_chain.append(Comp.Plain(text=remaining_text))
            elif need_prefix_zwsp:
                # 如果At组件在最后，且后面没有文本，添加一个仅包含零宽空格的Plain
                new_chain.append(Comp.Plain(text='\u200B'))

        result.chain = new_chain
