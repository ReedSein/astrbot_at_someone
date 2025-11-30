import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import LLMResponse  # 确认使用 LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.core.message.components import BaseMessageComponent

@register(
    "at_someone",
    "sasapp77",
    "让bot学会主动@别人，需要配合系统提示词",
    "1.0.7",
    ""
)
class AtSomeonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 预编译正则，提升匹配效率
        self.at_pattern = re.compile(r"<@(.*?)>")

    # -------------------------------------------------------------------------
    # Stage 1: 预处理 (Pre-processing)
    # 时机：在核心分段器工作之前
    # 作用：把换行符碾平为普通空格，骗过分段器，确保 At 和后面的话在同一条消息里。
    # -------------------------------------------------------------------------
    @filter.on_llm_response()
    async def flatten_newlines(self, event: AstrMessageEvent, resp: LLMResponse):
        if resp.completion_text:
            # 逻辑: 找到 <@...>，如果后面紧跟着任何空白字符序列(包括换行)，替换为单个普通空格
            # 这样 AstrBot 的 Splitter 就会认为这是一整句话
            resp.completion_text = re.sub(r'(<@.*?>)\s+', r'\1 ', resp.completion_text)

    # -------------------------------------------------------------------------
    # Stage 2: 渲染 (Rendering)
    # 时机：在分段之后，发送之前
    # 作用：解析 At，并插入零宽空格三明治，确保 QQ 客户端显示漂亮的间隔。
    # -------------------------------------------------------------------------
    @filter.on_decorating_result(priority=-200)
    async def handle_at_conversion(self, event: AstrMessageEvent):
        result = event.get_result()
        msg_chain = result.chain
        
        # 1. 快速性能检查
        has_tag = False
        for component in msg_chain:
            if isinstance(component, Comp.Plain) and "<@" in component.text:
                has_tag = True
                break
        
        if not has_tag:
            return

        new_chain: list[BaseMessageComponent] = []

        # 2. 私聊逻辑：移除标签
        if event.is_private_chat():
            for component in msg_chain:
                if isinstance(component, Comp.Plain):
                    cleaned_text = self.at_pattern.sub(r"\1", component.text)
                    if cleaned_text:
                        new_chain.append(Comp.Plain(text=cleaned_text))
                else:
                    new_chain.append(component)
            result.chain = new_chain
            return

        # 3. 群聊逻辑：获取群员信息
        group = await event.get_group()
        if not group or not group.members:
            return

        # 构建映射表：使用 getattr 安全访问属性
        members_map = {}
        for member in group.members:
            if member.nickname:
                members_map[member.nickname] = member.user_id
            
            # 安全获取 card 属性
            card = getattr(member, "card", None)
            if card:
                members_map[card] = member.user_id

        # 4. 重组消息链
        for component in msg_chain:
            if not isinstance(component, Comp.Plain):
                new_chain.append(component)
                continue

            text = component.text
            last_end = 0
            
            for match in self.at_pattern.finditer(text):
                start, end = match.span()
                
                # A. 添加标签前的文本
                if start > last_end:
                    new_chain.append(Comp.Plain(text=text[last_end:start]))

                content = match.group(1).strip()
                user_id_to_at = None

                # B. 解析 ID 或 昵称
                if content.isdigit():
                    user_id_to_at = int(content)
                elif content in members_map:
                    user_id_to_at = int(members_map[content])
                else:
                    try:
                        user_id_to_at = int(content)
                    except ValueError:
                        pass
                
                # C. 构建组件
                if user_id_to_at is not None:
                    # 1. 真实的 At
                    new_chain.append(Comp.At(qq=user_id_to_at))
                    
                    # 2. 【拟人化魔法】：零宽空格 + 普通空格 + 零宽空格
                    # 这确保了适配器保留中间的空格，且不会被分段器切分（因为分段早就不管这里了）
                    new_chain.append(Comp.Plain(text='\u200B \u200B')) 
                else:
                    # 解析失败回退
                    new_chain.append(Comp.Plain(text=match.group(0)))
                
                last_end = end

            # 5. 添加剩余文本
            if last_end < len(text):
                remaining_text = text[last_end:]
                # 去除 Stage 1 产生的粘合剂空格，防止双重空格
                remaining_text = remaining_text.lstrip()
                if remaining_text:
                    new_chain.append(Comp.Plain(text=remaining_text))

        # 5. 应用修改
        result.chain = new_chain
