# code_agent/runtime/context.py

import json
import time
from pathlib import Path

class ContextManager:
    def __init__(
        self,
        transcript_dir: Path,
        tool_results_dir: Path,
        context_limit: int = 50000,
        max_messages: int = 50,
        keep_recent_tool_results: int = 3,
        persist_threshold: int = 10000,
        model_provider=None,
        summary_max_tokens: int = 2000,
    ):
        self.transcript_dir = transcript_dir
        self.tool_results_dir = tool_results_dir
        self.context_limit = context_limit
        self.max_messages = max_messages
        self.keep_recent_tool_results = keep_recent_tool_results
        self.persist_threshold = persist_threshold
        self.model_provider = model_provider
        self.summary_max_tokens = summary_max_tokens

    def prepare(self, messages: list[dict]) -> list[dict]:
        """
        每次调用模型前都执行一次。
        """
        # 处理长输出
        messages = self.tool_result_budget(messages)
        # 将过早的工具结果压缩
        messages = self.micro_compact(messages)
        # 信息条数过多时,裁掉中间结果
        messages = self.snip_compact(messages)

        # 长度仍然超上限, 调用大模型将其总结压缩
        if self.estimate_size(messages) > self.context_limit:
            messages = self.llm_compact(messages)

        messages = self.sanitize_tool_result_messages(messages)
        return messages

    def estimate_size(self, messages: list[dict]) -> int:
        return len(json.dumps(messages, default=str, ensure_ascii=False))

    def collect_tool_results(self, messages: list[dict]):
        found = []

        for message_index, message in enumerate(messages):
            content = message.get("content")

            if message.get("role") != "user":
                continue

            if not isinstance(content, list):
                continue

            for block_index, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    found.append((message_index, block_index, block))

        return found

    def persist_large_output(self, tool_use_id: str, output: str) -> str:
        if len(output) <= self.persist_threshold:
            return output

        self.tool_results_dir.mkdir(parents=True, exist_ok=True)

        path = self.tool_results_dir / f"{tool_use_id}.txt"

        if not path.exists():
            path.write_text(output, encoding="utf-8")

        return (
            "<persisted-output>\n"
            f"Full output: {path}\n"
            f"Preview:\n{output[:2000]}\n"
            "</persisted-output>"
        )

    def tool_result_budget(self, messages: list[dict], max_bytes: int = 200_000) -> list[dict]:
        """
        如果最后一条 user 消息里的工具结果太大，就把大输出落盘，只保留预览。
        """
        if not messages:
            return messages

        last = messages[-1]
        content = last.get("content")

        if last.get("role") != "user" or not isinstance(content, list):
            return messages

        blocks = [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]

        total = sum(len(str(block.get("content", ""))) for block in blocks)

        if total <= max_bytes:
            return messages

        for block in sorted(
            blocks,
            key=lambda b: len(str(b.get("content", ""))),
            reverse=True,
        ):
            if total <= max_bytes:
                break

            text = str(block.get("content", ""))
            block["content"] = self.persist_large_output(
                block.get("tool_use_id", "unknown"),
                text,
            )

            total = sum(len(str(b.get("content", ""))) for b in blocks)

        return messages

    def micro_compact(self, messages: list[dict]) -> list[dict]:
        """
        只保留最近几个工具结果的完整内容，较早的大工具结果压缩成提示。
        """
        tool_results = self.collect_tool_results(messages)

        if len(tool_results) <= self.keep_recent_tool_results:
            return messages

        old_results = tool_results[:-self.keep_recent_tool_results]

        for _, _, block in old_results:
            content = str(block.get("content", ""))

            if len(content) > 120:
                block["content"] = "[Earlier tool result compacted. Re-run if needed.]"

        return messages

    def snip_compact(self, messages: list[dict]) -> list[dict]:
        """
        消息条数过多时，保留头部少量消息和尾部最近消息，中间裁掉。
        """
        if len(messages) <= self.max_messages:
            return messages

        keep_head = 3
        keep_tail = self.max_messages - keep_head

        snipped_count = len(messages) - keep_head - keep_tail

        return (
            messages[:keep_head]
            + [{
                "role": "user",
                "content": f"[snipped {snipped_count} messages]",
            }]
            + messages[-keep_tail:]
        )

    def write_transcript(self, messages: list[dict]) -> Path:
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

        path = self.transcript_dir / f"transcript_{int(time.time())}.jsonl"

        with path.open("w", encoding="utf-8") as f:
            for message in messages:
                f.write(json.dumps(message, default=str, ensure_ascii=False) + "\n")

        return path
    
    def summarize_history(self, messages: list[dict]) -> str:
        """
        调用大模型总结历史上下文。
        """
        if self.model_provider is None:
            return "Earlier conversation was compacted, but no model provider was configured for summarization."

        conversation = json.dumps(
            messages,
            default=str,
            ensure_ascii=False,
        )

        # 防止摘要 prompt 本身太大，先截断
        conversation = conversation[:80000]

        prompt = (
            "You are summarizing a coding-agent conversation so the agent can continue working.\n"
            "Please preserve the following information:\n"
            "1. The user's current goal.\n"
            "2. Important decisions already made.\n"
            "3. Files created, edited, deleted, or read.\n"
            "4. Tool results that affect future steps.\n"
            "5. User preferences and constraints.\n"
            "6. Remaining tasks or next steps.\n\n"
            "Write the summary in Chinese unless the original content is code or file paths.\n\n"
            "Conversation JSON:\n"
            f"{conversation}"
        )

        response = self.model_provider.generate(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            system=(
                "You are a context compaction assistant. "
                "Summarize accurately and concisely. "
                "Do not invent facts."
            ),
            tools=[],
            max_tokens=self.summary_max_tokens,
        )

        return self.extract_text(response.content) or "(empty summary)"
    
    def extract_text(self, content) -> str:
        if not isinstance(content, list):
            return str(content)

        texts = []

        for block in content:
            if getattr(block, "type", None) == "text":
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))

        return "\n".join(texts).strip()
    
    def llm_compact(self, messages: list[dict]) -> list[dict]:
        """
        大模型摘要版 compact：
        1. 保存完整 transcript
        2. 调用模型总结历史
        3. 用摘要替代旧上下文
        4. 保留最近若干条消息
        """
        transcript = self.write_transcript(messages)

        try:
            summary = self.summarize_history(messages)
        except Exception as e:
            summary = (
                "Earlier conversation was compacted, but summarization failed.\n"
                f"Error: {type(e).__name__}: {e}"
            )

        return [
            {
                "role": "user",
                "content": (
                    "[Context compacted]\n\n"
                    f"Transcript saved to: {transcript}\n\n"
                    "Summary of earlier conversation:\n"
                    f"{summary}"
                ),
            },
            *messages[-10:],
        ]
    
    def sanitize_tool_result_messages(self, messages: list[dict]) -> list[dict]:
        """
        Anthropic 要求：
        user 的 tool_result 必须紧跟在包含对应 tool_use 的 assistant 消息之后。

        如果历史被 compact/snip 后破坏了这个结构，就把孤立 tool_result
        转成普通 text，避免 API 400。
        """
        sanitized = []

        for i, msg in enumerate(messages):
            content = msg.get("content")

            if msg.get("role") != "user" or not isinstance(content, list):
                sanitized.append(msg)
                continue

            tool_results = [
                block for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]

            if not tool_results:
                sanitized.append(msg)
                continue

            previous = sanitized[-1] if sanitized else None

            # tool_result和tool_use成对检验
            if not self._previous_message_has_matching_tool_use(previous, tool_results):
                # 不满足，转普通text
                text = self._tool_results_to_text(tool_results)
                sanitized.append({
                    "role": "user",
                    "content": (
                        "[Recovered orphaned tool results]\n"
                        f"{text}"
                    ),
                })
                continue

            sanitized.append(msg)

        return sanitized

    def _previous_message_has_matching_tool_use(self, previous: dict | None, tool_results: list[dict]) -> bool:
        if not previous:
            return False

        if previous.get("role") != "assistant":
            return False

        content = previous.get("content")

        if not isinstance(content, list):
            return False

        tool_use_ids = set()

        for block in content:
            if getattr(block, "type", None) == "tool_use":
                tool_use_ids.add(block.id)
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use_ids.add(block.get("id"))

        for result in tool_results:
            if result.get("tool_use_id") not in tool_use_ids:
                return False

        return True

    def _tool_results_to_text(self, tool_results: list[dict]) -> str:
        lines = []

        for result in tool_results:
            tool_use_id = result.get("tool_use_id", "unknown")
            content = result.get("content", "")
            lines.append(f"- tool_use_id={tool_use_id}: {content}")

        return "\n".join(lines)