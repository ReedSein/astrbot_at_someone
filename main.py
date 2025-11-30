import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderResponse
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.core.message.components import BaseMessageComponent

@register(
    "at_someone",
    "sasapp77 & ReedSein",
    "让bot学会主动@别人，需要配合系统提示词",
    "1.0.3",
    ""
)
class AtSomeonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 预编译正则，提升匹配效率
        # 匹配 <@...> 格式
        self.at_pattern = re.compile(r"<@(.*?)>")

    # -------------------------------------------------------------------------
    # Stage 1: 预处理 (Pre-processing) / 粘合剂
    # -------------------------------------------------------------------------
    @filter.on_llm_response()
    async def flatten_newlines(self, event: AstrMessageEvent, resp: ProviderResponse):
        """
        在 AstrBot 核心分段器介入之前，清理 LLM 的输出。
        将 <@xxx> 后面紧跟的换行符(\n)、制表符(\t)等统一替换为单个空格。
        目的：防止 AstrBot 因为检测到换行符而将 At 组件和后续文本切分为两条消息。
        """
        if resp.completion_text:
            # 逻辑: 找到 <@...>，如果后面紧跟着任何空白字符序列，替换为单个空格
            resp.completion_text = re.sub(r'(<@.*?>)\s+', r'\1 ', resp.completion_text)

    # -------------------------------------------------------------------------
    # Stage 2: 渲染 (Rendering) / 拟人化处理
    # -------------------------------------------------------------------------
    @filter.on_decorating_result(priority=-200)
    async def handle_at_conversion(self, event: AstrMessageEvent):
        """
        将文本中的 <@xxx> 标签转换为真实的 At 消息组件，并添加拟人化的空格。
        """
        result = event.get_result()
        msg_chain = result.chain
        
        # 1. 快速检查：如果消息链中没有 Plain 文本包含 "<@"，直接返回
        # 这一步能大幅减少不必要的计算，提升高并发下的性能
        has_tag = False
        for component in msg_chain:
            if isinstance(component, Comp.Plain) and "<@" in component.text:
                has_tag = True
                break
        
        if not has_tag:
            return

        new_chain: list[BaseMessageComponent] = []

        # 2. 私聊逻辑：移除标签，保持文本整洁
        if event.is_private_chat():
            for component in msg_chain:
                if isinstance(component, Comp.Plain):
                    # 仅保留名字，移除 <@ > 符号
                    cleaned_text = self.at_pattern.sub(r"\1", component.text)
                    if cleaned_text:
                        new_chain.append(Comp.Plain(text=cleaned_text))
                else:
                    new_chain.append(component)
            result.chain = new_chain
            return

        # 3. 群聊逻辑：准备映射表
        group = await event.get_group()
        # 防御性编程：如果获取不到群信息，不做处理
        if not group or not group.members:
            return

        # 构建映射表：优先使用 card (群名片)，其次使用 nickname (昵称)
        # 这样 LLM 呼叫用户的群名片也能成功 @
        members_map = {}
        for member in group.members:
            if member.nickname:
                members_map[member.nickname] = member.user_id
            if member.card:
                members_map[member.card] = member.user_id

        # 4. 遍历并重组消息链
        for component in msg_chain:
            # 只处理纯文本组件，保留图片/语音等其他组件
            if not isinstance(component, Comp.Plain):
                new_chain.append(component)
                continue

            text = component.text
            last_end = 0
            
            # 查找所有 <@...> 匹配项
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
                    # 尝试处理类似 "123456" 这种纯数字字符串
                    try:
                        user_id_to_at = int(content)
                    except ValueError:
                        pass # 确实找不到
                
                # C. 构建组件
                if user_id_to_at is not None:
                    # 插入 At 组件
                    new_chain.append(Comp.At(qq=user_id_to_at))
                    
                    # 【拟人化关键】：插入一个标准的半角空格
                    # 官方客户端在 @ 后会自动跟一个空格
                    new_chain.append(Comp.Plain(text=' ')) 
                else:
                    # 解析失败（找不到人），回退为原始文本，提示用户/LLM 出错了
                    logger.warning(f"Plugin 'at_someone': 无法在群 {group.group_id} 找到用户 '{content}'")
                    new_chain.append(Comp.Plain(text=match.group(0)))
                
                last_end = end

            # 5. 添加剩余文本
            if last_end < len(text):
                remaining_text = text[last_end:]
                
                # 【细节优化】：
                # 因为我们在 Stage 1 (on_llm_response) 中把换行符替换成了空格，
                # 所以 remaining_text 很有可能以空格开头。
                # 而我们在上面步骤 C 中已经手动插入了一个“拟人化空格”。
                # 为了避免双重空格 (At  Text)，我们需要去除 remaining_text 左侧的空白。
                remaining_text = remaining_text.lstrip()
                
                if remaining_text:
                    new_chain.append(Comp.Plain(text=remaining_text))

        # 5. 应用修改
        result.chain = new_chain
