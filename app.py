"""Local RAG chat application for the PRC law knowledge base."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
KNOWLEDGE_PATH = ROOT / "knowledge-base" / "prc_law_articles.jsonl"
CHINESE_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]{2,}")
SPACE_RE = re.compile(r"\s+")
TITLE_CLEAN_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


@dataclass(frozen=True)
class Article:
    id: str
    document_id: str
    document_title: str
    source_file: str
    source_pages: list[int]
    hierarchy: dict[str, str]
    article: str
    text: str
    normalized_text: str

    def public(self, score: float | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "document_title": self.document_title,
            "source_file": self.source_file,
            "source_pages": self.source_pages,
            "hierarchy": self.hierarchy,
            "article": self.article,
            "text": self.text,
        }
        if score is not None:
            result["score"] = round(score, 3)
        return result


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, str]]

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def preview(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "user":
                return message.get("content", "")[:42]
        return ""

    def summary(self, active_id: str | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "preview": self.preview,
            "active": self.id == active_id,
        }

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "preview": self.preview,
            "messages": self.messages,
        }


class LegalKnowledgeBase:
    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Knowledge base not found: {path}")
        self.articles = self._load(path)
        if not self.articles:
            raise RuntimeError("The knowledge base does not contain any article chunks.")

    @staticmethod
    def _load(path: Path) -> list[Article]:
        articles: list[Article] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                text = SPACE_RE.sub("", row["text"])
                articles.append(
                    Article(
                        id=row["id"],
                        document_id=row["document_id"],
                        document_title=row["document_title"],
                        source_file=row["source_file"],
                        source_pages=row["source_pages"],
                        hierarchy=row.get("hierarchy", {}),
                        article=row["article"],
                        text=row["text"],
                        normalized_text=text,
                    )
                )
        return articles

    @staticmethod
    def _terms(query: str) -> set[str]:
        terms: set[str] = set()
        normalized = SPACE_RE.sub("", query)
        for run in CHINESE_RUN_RE.findall(normalized):
            # Long direct phrases are highly useful; n-grams retain recall for natural language questions.
            if len(run) <= 18:
                terms.add(run)
            for width in range(2, min(6, len(run)) + 1):
                terms.update(run[index : index + width] for index in range(len(run) - width + 1))
        terms.update(token.lower() for token in LATIN_TOKEN_RE.findall(query))
        return {term for term in terms if term not in {"什么", "怎么", "可以", "是否", "如何", "因为", "我们", "你们"}}

    def search(self, query: str, limit: int = 8) -> list[Article]:
        terms = self._terms(query)
        if not terms:
            return self.articles[:limit]
        ranked: list[tuple[float, Article]] = []
        for article in self.articles:
            score = 0.0
            haystack = article.normalized_text
            title = article.document_title + article.article + " ".join(article.hierarchy.values())
            for term in terms:
                if term in haystack:
                    occurrences = haystack.count(term)
                    score += len(term) ** 1.65 * (1 + min(occurrences - 1, 2) * 0.15)
                if term in title:
                    score += len(term) ** 1.8
            if score:
                ranked.append((score, article))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [article for _, article in ranked[:limit]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_conversation_title(text: str) -> str:
    cleaned = TITLE_CLEAN_RE.sub("", text).strip()
    if not cleaned:
        return "新对话"
    title = cleaned[:18]
    return title if len(cleaned) <= 18 else f"{title}…"


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.active_id: str | None = None
        self.conversations: dict[str, Conversation] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.active_id = raw.get("active_id")
            for row in raw.get("conversations", []):
                conversation = Conversation(
                    id=row["id"],
                    title=row.get("title", "新对话"),
                    created_at=row.get("created_at", utc_now()),
                    updated_at=row.get("updated_at", utc_now()),
                    messages=row.get("messages", []),
                )
                self.conversations[conversation.id] = conversation
        if self.active_id not in self.conversations:
            self.active_id = next(iter(self.conversations), None)

    def _payload(self) -> dict[str, Any]:
        ordered = sorted(self.conversations.values(), key=lambda item: item.updated_at, reverse=True)
        return {
            "active_id": self.active_id,
            "conversations": [
                {
                    "id": conversation.id,
                    "title": conversation.title,
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                    "messages": conversation.messages,
                }
                for conversation in ordered
            ],
        }

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(self._payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    def list(self) -> list[Conversation]:
        with self.lock:
            return sorted(self.conversations.values(), key=lambda item: item.updated_at, reverse=True)

    def get(self, conversation_id: str) -> Conversation | None:
        with self.lock:
            return self.conversations.get(conversation_id)

    def ensure_active(self) -> Conversation:
        with self.lock:
            if self.active_id and self.active_id in self.conversations:
                return self.conversations[self.active_id]
            return self.create_conversation()

    def create_conversation(self, title: str | None = None) -> Conversation:
        with self.lock:
            conversation = Conversation(
                id=uuid.uuid4().hex,
                title=(title or "新对话").strip() or "新对话",
                created_at=utc_now(),
                updated_at=utc_now(),
                messages=[],
            )
            self.conversations[conversation.id] = conversation
            self.active_id = conversation.id
            self._save()
            return conversation

    def set_active(self, conversation_id: str) -> Conversation:
        with self.lock:
            conversation = self.conversations[conversation_id]
            self.active_id = conversation_id
            self._save()
            return conversation

    def append_message(self, conversation_id: str, role: str, content: str) -> Conversation:
        with self.lock:
            conversation = self.conversations[conversation_id]
            conversation.messages.append({"role": role, "content": content})
            conversation.updated_at = utc_now()
            self.active_id = conversation.id
            self._save()
            return conversation

    def rename_from_first_user_message(self, conversation_id: str) -> Conversation:
        with self.lock:
            conversation = self.conversations[conversation_id]
            if conversation.title in {"新对话", "未命名对话"}:
                first_user = next((message["content"] for message in conversation.messages if message.get("role") == "user"), "")
                if first_user:
                    conversation.title = make_conversation_title(first_user)
                    conversation.updated_at = utc_now()
                    self._save()
            return conversation

    def pop_last_message(self, conversation_id: str) -> dict[str, str] | None:
        with self.lock:
            conversation = self.conversations[conversation_id]
            if not conversation.messages:
                return None
            removed = conversation.messages.pop()
            conversation.updated_at = utc_now()
            self._save()
            return removed

    def delete_conversation(self, conversation_id: str) -> Conversation | None:
        with self.lock:
            if conversation_id not in self.conversations:
                raise KeyError(conversation_id)
            was_active = self.active_id == conversation_id
            del self.conversations[conversation_id]
            if self.conversations:
                if was_active or self.active_id not in self.conversations:
                    remaining = sorted(self.conversations.values(), key=lambda item: item.updated_at, reverse=True)
                    self.active_id = remaining[0].id
            else:
                self.active_id = None
            self._save()
            return self.conversations.get(self.active_id) if self.active_id else None


def build_system_prompt(sources: list[Article]) -> str:
    source_text = "\n\n".join(
        f"[{index}] {source.document_title} {source.article}"
        f"（{' / '.join(source.hierarchy.values()) or '未标注章节'}，来源 PDF 第 {', '.join(map(str, source.source_pages))} 页）\n"
        f"{source.text}"
        for index, source in enumerate(sources, start=1)
    )
    return f"""你是“法析”，一名面向中国大陆法律的初步法律风险分析助手，按照中国大陆执业律师做初步风险分析的方式回答。

工作边界：
- 根据用户提供的事实与下方已检索法条，分析可能涉及的法律问题、构成条件及可能后果。
- 仅将资料中明确支持的结论说成“可能涉及”；事实不完整时，清楚说明不确定性和需要补充的关键事实。
- 不得编造法条、条文内容、处罚金额、时限、司法判例或具体办案结果。资料未覆盖时，请直说“当前知识库未检索到足以支持的具体依据”。
- 严格区分民事责任、行政责任、刑事风险、劳动/合同后果。不要承诺结果。
- 不协助毁灭、伪造、倒签证据，规避监管或报复他人。需要时建议合法保全原始记录、注意期限并咨询执业律师。
- 这不是正式法律意见。涉及刑事风险、重大财产、劳动仲裁/诉讼期限或紧急人身风险时，应建议尽快咨询中国大陆执业律师或联系有关部门。
- 结尾建议必须像律师给客户的下一步建议：优先写证据保全、书面沟通、期限、投诉/仲裁/诉讼路径，而不是空泛口号。

输出要求：
- 使用简体中文，语气克制、专业、像律师的初步书面意见。
- 不要输出 Markdown 强调符号、斜体、粗体、横线分隔线或多层嵌套列表。
- 不要在编号段落之间空一行；每个编号下如需分点，直接接着写，不要再多留空白行。
- 用纯文本编号段落即可，结构固定为：
  1. 初步判断
  2. 可能涉及的规定
  3. 可能后果
  4. 仍需确认的事实或证据
  5. 建议的下一步
- 每个部分都要短句、直说，不要长篇重复。
- 如果只能做初步判断，就明确说“目前只能判断为可能/倾向于/需要进一步核实”，不要写成定论。

本轮可引用资料：
{source_text}
"""


def build_model_messages(history: list[dict[str, str]], sources: list[Article]) -> list[dict[str, str]]:
    system_prompt = build_system_prompt(sources)
    recent_history = history[-18:]
    return [{"role": "system", "content": system_prompt}] + recent_history


def parse_extra_headers(raw_headers: Any) -> dict[str, str]:
    if raw_headers in (None, "", {}):
        return {}
    if isinstance(raw_headers, dict):
        headers = raw_headers
    elif isinstance(raw_headers, str):
        headers = json.loads(raw_headers)
    else:
        raise ValueError("额外请求头必须是 JSON 对象。")
    if not isinstance(headers, dict):
        raise ValueError("额外请求头必须是 JSON 对象。")
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("额外请求头的键和值都必须是字符串。")
        key = key.strip()
        value = value.strip()
        if key and value:
            normalized[key] = value
    return normalized


def request_completion(
    config: dict[str, str],
    messages: list[dict[str, str]],
    sources: list[Article],
    extra_headers: dict[str, str] | None = None,
) -> str:
    endpoint = config["base_url"].rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = f"{endpoint}/chat/completions"
    elif not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/v1/chat/completions"
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.2,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            **(extra_headers or {}),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:800]
        if error.code == 403 and ("1010" in detail or "access denied" in detail.lower()):
            raise ValueError(
                "上游服务拒绝了请求（403 / 1010）。这通常不是对话内容问题，而是 API 地址、网络出口、代理、账号权限或服务商风控问题。"
                "如果你用的是中转站，优先核对 base_url、模型名、Key，以及它是否要求额外请求头（如 Referer、Origin、X-API-Key）。"
                "如果直接连官方接口，请确认 base_url 是否为 `https://api.openai.com/v1` 且 Key 具备对应模型权限。"
            ) from error
        raise ValueError(f"API 请求失败（HTTP {error.code}）：{detail}") from error
    except URLError as error:
        raise ValueError(f"无法连接 API：{error.reason}") from error
    except TimeoutError as error:
        raise ValueError("API 请求超时，请检查网络或稍后重试。") from error
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as error:
        raise ValueError("API 返回格式不符合 OpenAI Chat Completions 规范。") from error


class AppHandler(SimpleHTTPRequestHandler):
    knowledge_base: LegalKnowledgeBase
    conversation_store: ConversationStore

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            active = self.conversation_store.get(self.conversation_store.active_id) if self.conversation_store.active_id else None
            self._send_json(
                {
                    "article_count": len(self.knowledge_base.articles),
                    "status": "ready",
                    "active_conversation_id": active.id if active else None,
                    "conversation_count": len(self.conversation_store.list()),
                }
            )
            return
        if parsed.path == "/api/conversations":
            active_id = self.conversation_store.active_id
            self._send_json(
                {
                    "active_conversation_id": active_id,
                    "conversations": [conversation.summary(active_id) for conversation in self.conversation_store.list()],
                }
            )
            return
        if parsed.path.startswith("/api/conversations/"):
            conversation_id = parsed.path.rsplit("/", 1)[-1]
            conversation = self.conversation_store.get(conversation_id)
            if conversation is None:
                self._send_json({"error": "会话不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json({"conversation": conversation.public()})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/conversations":
                body = self._read_json()
                title = body.get("title") if isinstance(body, dict) else None
                conversation = self.conversation_store.create_conversation(title if isinstance(title, str) else None)
                self._send_json({"conversation": conversation.public(), "active_conversation_id": conversation.id})
                return
            if parsed.path == "/api/chat":
                body = self._read_json()
                config = body.get("config", {})
                conversation_id = body.get("conversation_id")
                message = body.get("message")
                extra_headers = parse_extra_headers(body.get("extra_headers"))
                self._validate_chat_request(config, conversation_id, message)
                conversation = self._resolve_conversation(conversation_id)
                user_text = str(message).strip()
                self.conversation_store.append_message(conversation.id, "user", user_text)
                try:
                    sources = self.knowledge_base.search(user_text)
                    refreshed = self.conversation_store.get(conversation.id)
                    assert refreshed is not None
                    model_messages = build_model_messages(refreshed.messages, sources)
                    answer = request_completion(config, model_messages, sources, extra_headers)
                    refreshed = self.conversation_store.append_message(conversation.id, "assistant", answer)
                    refreshed = self.conversation_store.rename_from_first_user_message(conversation.id)
                    self._send_json(
                        {
                            "answer": answer,
                            "sources": [source.public() for source in sources],
                            "conversation": refreshed.public(),
                            "active_conversation_id": refreshed.id,
                            "conversations": [item.summary(self.conversation_store.active_id) for item in self.conversation_store.list()],
                        }
                    )
                    return
                except Exception:
                    self.conversation_store.pop_last_message(conversation.id)
                    raise
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except ValueError as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # Keep unexpected details out of the browser response.
            print(f"Unexpected error: {error}", file=sys.stderr)
            self._send_json({"error": "本地服务发生意外错误，请查看启动终端。"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/conversations/"):
                conversation_id = parsed.path.rsplit("/", 1)[-1]
                active = self.conversation_store.delete_conversation(conversation_id)
                self._send_json(
                    {
                        "deleted_conversation_id": conversation_id,
                        "active_conversation_id": active.id if active else None,
                        "conversations": [item.summary(active.id if active else None) for item in self.conversation_store.list()],
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except KeyError:
            self._send_json({"error": "会话不存在。"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            print(f"Unexpected error: {error}", file=sys.stderr)
            self._send_json({"error": "删除会话失败，请查看启动终端。"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("请求内容为空或过大。")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _resolve_conversation(self, conversation_id: str | None) -> Conversation:
        if isinstance(conversation_id, str) and conversation_id.strip():
            conversation = self.conversation_store.get(conversation_id)
            if conversation is None:
                raise ValueError("指定的会话不存在。")
            return self.conversation_store.set_active(conversation.id)
        active = self.conversation_store.get(self.conversation_store.active_id) if self.conversation_store.active_id else None
        return active if active is not None else self.conversation_store.create_conversation()

    @staticmethod
    def _validate_chat_request(config: dict[str, str], conversation_id: str | None, message: Any) -> None:
        if not all(isinstance(config.get(field), str) and config[field].strip() for field in ("base_url", "model", "api_key")):
            raise ValueError("请在设置中填写 API 地址、模型名称和 API Key。")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise ValueError("会话编号无效。")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("请输入需要分析的情况。")
        if len(message) > 6000:
            raise ValueError("输入内容过长。")

    def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local PRC law RAG chat app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    AppHandler.knowledge_base = LegalKnowledgeBase(KNOWLEDGE_PATH)
    AppHandler.conversation_store = ConversationStore(CONVERSATIONS_PATH)
    mimetypes.add_type("application/javascript", ".js")
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"法析本地服务已启动：http://{args.host}:{args.port}")
    print(f"已加载 {len(AppHandler.knowledge_base.articles)} 条法条切片。按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
