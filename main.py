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
    "1.0.6",
    ""
)
class AtSomeonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 预编译正则，提升匹配效率
        self.at_pattern = re.compile(r"<@(.*?)>")

    # -------------------------------------------------------------------------
    # Stage 1: 预处理 (Pre-processing) / 粘合剂 + 强制空格
    # -------------------------------------------------------------------------
    @filter.on_llm_response()
    async def flatten_newlines(self, event: AstrMessageEvent, resp: LLMResponse):
        """
        1. 防止分段：将换行符替换为空格。
        2. 强制间隔：将 <@xxx> 后面的任意空白（或无空白）强制替换为一个标准空格。
        """
        if resp.completion_text:
            # 解释：\s* 匹配0个或多个空白。
            # 无论 LLM 输出 "<@user>text" 还是 "<@user>\ntext"
            # 都会被替换为 "<@user> text" (注意中间有个空格)
            resp.completion_text = re.sub(r'(<@.*?>)\s*', r'\1 ', resp.completion_text)

    # -------------------------------------------------------------------------
    # Stage 2: 渲染 (Rendering)
    # -------------------------------------------------------------------------
    @filter.on_decorating_result(priority=-200)
    async def handle_at_conversion(self, event: AstrMessageEvent):
        """
        将文本中的 <@xxx> 标签转换为真实的 At 消息组件。
        """
        result = event.get_result()
        msg_chain = result.chain
        
        # 1. 快速检查
        has_tag = False
        for component in msg_chain:
            if isinstance(component, Comp.Plain) and "<@" in component.text:
                has_tag = True
                break
        
        if not has_tag:
            return

        new_chain: list[BaseMessageComponent] = []

        # 2. 私聊逻辑
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

        # 3. 群聊逻辑
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

        # 4. 遍历并重组消息链
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
                    new_chain.append(Comp.At(qq=user_id_to_at))
                    # 【重要修改】
                    # 这里不再单独添加 Comp.Plain(text=' ') 组件
                    # 因为单独的空格组件容易被客户端吞掉。
                    # 我们依赖 Stage 1 强制生成的空格，让它留在 remaining_text 的开头。
                else:
                    # 解析失败
                    new_chain.append(Comp.Plain(text=match.group(0)))
                
                last_end = end

            # 5. 添加剩余文本
            if last_end < len(text):
                remaining_text = text[last_end:]
                
                # 【重要修改】
                # 删除了 .lstrip()。
                # 因为 Stage 1 保证了 remaining_text 一定是以空格开头的 (例如 " 嗯。快点...")
                # 我们保留这个空格，让它作为 Plain 文本的一部分发送。
                # 这样客户端就会乖乖渲染出 "[At] 嗯。快点..." (中间有空隙)
                
                if remaining_text:
                    new_chain.append(Comp.Plain(text=remaining_text))

        # 5. 应用修改
        result.chain = new_chain
