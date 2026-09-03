# 法析

一个面向熟读民法典和劳动法的工作台。

## 包含什么

- 本地网页界面
- 劳动法 + 民法典 RAG 知识库
- 会话历史本地存储
- 可导出的 Codex skill：`china-legal-risk`

## 怎么用

1. 启动 `run-local.bat`
2. 打开 `http://127.0.0.1:8767`
3. 进入 `API 设置`
4. 填你的 API 地址、模型名、API Key
5. 如使用中转站，可把额外请求头写进 JSON

示例：

```json
{
  "X-API-Key": "你的key",
  "Referer": "https://你的站点",
  "Origin": "https://你的站点"
}
```

## 适合的 API

- OpenAI 兼容 `chat/completions` 接口
- 中转站提供的兼容接口

## 本地文件

- `app.py`：本地 RAG 服务
- `web/`：前端页面
- `knowledge-base/`：法条切片
- `china-legal-risk/`：可复用 skill

## 说明

会话历史保存在本机 `data/conversations.json`。
API 配置会保存在浏览器本地存储中，刷新后仍可使用。

