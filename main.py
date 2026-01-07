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
    "sasapp77 & ReedSein",            # 插件作者
    "让bot学会主动@别人，需要配合系统提示词",  # 插件描述
    "2.0.0",               # 插件版本
    "https://github.com/ReedSein/astrbot_at_someone"                     # 插件仓库地址
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
        if result is None or not result.chain:
            return

        msg_chain = result.chain
        new_chain: list[BaseMessageComponent] = []

        # 私聊没有@功能，遍历并移除所有At元素
        if event.is_private_chat():
            has_at_tag = any(
                isinstance(component, Comp.Plain) and "<@" in component.text
                for component in msg_chain
            )
            has_at_component = any(
                isinstance(component, (Comp.At, Comp.AtAll))
                for component in msg_chain
            )
            if not has_at_tag and not has_at_component:
                return

            for component in msg_chain:
                if isinstance(component, (Comp.At, Comp.AtAll)):
                    continue

                if isinstance(component, Comp.Plain) and "<@" in component.text:
                    convert = getattr(component, "convert", True)
                    cleaned_text = self.at_pattern.sub("", component.text)
                    if cleaned_text.strip():
                        new_chain.append(
                            Comp.Plain(text=cleaned_text, convert=convert),
                        )
                else:
                    new_chain.append(component)
            result.chain = new_chain
            return

        has_at_tag = any(
            isinstance(component, Comp.Plain) and "<@" in component.text
            for component in msg_chain
        )
        if not has_at_tag:
            return

        group_id_for_log = event.get_group_id() or event.unified_msg_origin
        members_map: dict[str, str] | None = None
        members_map_failed = False

        for component in msg_chain:
            if not isinstance(component, Comp.Plain):
                new_chain.append(component)
                continue

            if "<@" not in component.text:
                new_chain.append(component)
                continue

            convert = getattr(component, "convert", True)
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
                    new_chain.append(
                        Comp.Plain(text=prefix_text, convert=convert),
                    )

                content = match.group(1).strip()
                user_id_to_at = None

                if content.isdigit():
                    user_id_to_at = int(content)
                else:
                    if members_map is None and not members_map_failed:
                        try:
                            group = await event.get_group()
                        except Exception as e:
                            logger.warning(
                                f"获取群信息失败，无法解析 @昵称：{e}",
                            )
                            members_map_failed = True
                            members_map = {}
                        else:
                            if group and group.members:
                                members_map = {
                                    member.nickname: str(member.user_id)
                                    for member in group.members
                                    if member.nickname
                                }
                            else:
                                logger.warning(
                                    f"未获取到群成员列表，无法解析 @昵称：group_id={group_id_for_log}",
                                )
                                members_map_failed = True
                                members_map = {}

                    if members_map and content in members_map:
                        user_id_to_at = int(members_map[content])
                    elif not members_map_failed:
                        logger.warning(
                            f"在群 '{group_id_for_log}' 中无法找到昵称为 '{content}' 的用户，已跳过@。",
                        )
                
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
                    new_chain.append(
                        Comp.Plain(text=suffix_text, convert=convert),
                    )
                
                last_end = end

            if last_end < len(text):
                # 添加剩余文本
                remaining_text = text[last_end:]
                if need_prefix_zwsp:
                    remaining_text = '\u200B' + remaining_text
                    need_prefix_zwsp = False
                new_chain.append(
                    Comp.Plain(text=remaining_text, convert=convert),
                )
            elif need_prefix_zwsp:
                # 如果At组件在最后，且后面没有文本，添加一个仅包含零宽空格的Plain
                new_chain.append(
                    Comp.Plain(text='\u200B', convert=convert),
                )

        result.chain = new_chain
