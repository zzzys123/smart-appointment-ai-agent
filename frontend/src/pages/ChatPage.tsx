import { useEffect, useRef, useState } from "react";
import {
  ArrowUpOutlined,
  BulbOutlined,
  CopyOutlined,
  CustomerServiceOutlined,
  DeleteOutlined,
  RedoOutlined,
  RobotOutlined,
  StopOutlined,
  UserOutlined
} from "@ant-design/icons";
import { Alert, Avatar, Button, Card, Input, Space, Tag, Typography, message } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../types";
import { getErrorMessage } from "../api";
import { parseAgentStream } from "../chatProtocol";

const suggestions = [
  { icon: <CustomerServiceOutlined />, text: "推荐一个适合久坐肩颈酸痛的项目" },
  { icon: <BulbOutlined />, text: "我想明天下午预约一位擅长推拿的女技师" },
  { icon: <RobotOutlined />, text: "介绍一下门店营业时间和取消规则" }
];

const initialMessage: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content: "你好，我是智约 AI 助手。你可以咨询服务、了解技师，或者直接告诉我预约时间和需求。"
};

function createId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = localStorage.getItem("smart-appointment-chat-history");
      const parsed = saved ? JSON.parse(saved) as ChatMessage[] : [];
      const migrated = parsed.map((item) => {
        if (item.role !== "assistant" || item.thoughts) return item;
        const result = parseAgentStream(item.content);
        return {
          ...item,
          content: result.reply,
          thoughts: result.thoughts,
          agent: result.agent,
          sources: result.sources
        };
      });
      return migrated.length ? migrated : [initialMessage];
    } catch {
      return [initialMessage];
    }
  });
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string>();
  const controllerRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    const completed = messages.filter((item) => !item.pending).slice(-40);
    localStorage.setItem("smart-appointment-chat-history", JSON.stringify(completed));
  }, [messages]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const sendMessage = async (preset?: string) => {
    const content = (preset ?? input).trim();
    if (!content || streaming) return;

    const assistantId = createId();
    setInput("");
    setError(undefined);
    setStreaming(true);
    setMessages((current) => [
      ...current,
      { id: createId(), role: "user", content },
      { id: assistantId, role: "assistant", content: "", pending: true }
    ]);

    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const sessionId = localStorage.getItem("smart-appointment-session");
      const response = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: content, session_id: sessionId }),
        signal: controller.signal
      });
      if (!response.ok || !response.body) {
        throw new Error(`聊天请求失败（${response.status}）`);
      }
      const nextSessionId = response.headers.get("X-Session-ID");
      if (nextSessionId) localStorage.setItem("smart-appointment-session", nextSessionId);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullText = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        fullText += decoder.decode(value, { stream: true });
        const parsed = parseAgentStream(fullText);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  content: parsed.reply,
                  thoughts: parsed.thoughts,
                  agent: parsed.agent,
                  sources: parsed.sources,
                  pending: !parsed.reply
                }
              : item
          )
        );
      }
      if (fullText.startsWith("[ERROR]")) throw new Error(fullText.replace("[ERROR]", ""));
      const completed = parseAgentStream(fullText);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId
            ? {
                ...item,
                content: completed.reply || "处理已完成，但没有返回可展示的回复。",
                thoughts: completed.thoughts,
                agent: completed.agent,
                sources: completed.sources,
                pending: false
              }
            : item
        )
      );
    } catch (requestError) {
      if ((requestError as Error).name !== "AbortError") {
        const errorText = getErrorMessage(requestError);
        setError(errorText);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantId
              ? { ...item, content: "抱歉，本次回复未能完成，请稍后重试。", pending: false }
              : item
          )
        );
      }
    } finally {
      setStreaming(false);
      controllerRef.current = null;
    }
  };

  const stop = () => {
    controllerRef.current?.abort();
    setStreaming(false);
    message.info("已停止生成");
  };

  const clearConversation = () => {
    controllerRef.current?.abort();
    localStorage.removeItem("smart-appointment-session");
    localStorage.removeItem("smart-appointment-chat-history");
    setMessages([initialMessage]);
    setStreaming(false);
    setError(undefined);
    message.success("已开始新的会话");
  };

  const askAgain = () => {
    const lastUserMessage = [...messages].reverse().find((item) => item.role === "user");
    if (lastUserMessage) void sendMessage(lastUserMessage.content);
  };

  const copyMessage = async (content: string) => {
    await navigator.clipboard.writeText(content);
    message.success("回答已复制");
  };

  return (
    <div className="chat-page">
      <section className="chat-hero">
        <div>
          <Space wrap>
            <Tag bordered={false} color="purple">多 Agent 智能调度</Tag>
            {messages.some((item) => item.role === "user") && <Button ghost size="small" icon={<DeleteOutlined />} onClick={clearConversation}>新会话</Button>}
          </Space>
          <Typography.Title>今天想安排什么服务？</Typography.Title>
          <Typography.Paragraph>
            从需求咨询到技师匹配与预约确认，一次对话即可完成。
          </Typography.Paragraph>
        </div>
        <div className="hero-orb"><RobotOutlined /></div>
      </section>

      <Card className="chat-card" styles={{ body: { padding: 0 } }}>
        <div className="message-list">
          {messages.map((item) => (
            <div key={item.id} className={`chat-message ${item.role}`}>
              <Avatar
                icon={item.role === "assistant" ? <RobotOutlined /> : <UserOutlined />}
                className="message-avatar"
              />
              <div className="message-body">
                <Typography.Text type="secondary">
                  {item.role === "assistant" ? (item.agent || "智约助手") : "你"}
                </Typography.Text>
                <div className={`message-bubble ${item.pending ? "is-pending" : ""}`}>
                  {item.pending ? (
                    <span className="typing-dots"><i /><i /><i /></span>
                  ) : item.role === "assistant" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
                  ) : item.content}
                </div>
                {item.role === "assistant" && Boolean(item.thoughts?.length) && (
                  <details className="thought-process">
                    <summary>查看处理过程 · {item.thoughts?.length} 个步骤</summary>
                    <div>
                      {item.thoughts?.map((thought, index) => (
                        <div className="thought-step" key={`${thought.agent}-${index}`}>
                          <Tag bordered={false}>{thought.agent}</Tag>
                          <span>{thought.content}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                {item.role === "assistant" && Boolean(item.sources?.length) && (
                  <div className="citation-panel">
                    <Typography.Text strong>参考来源</Typography.Text>
                    <div className="citation-list">
                      {item.sources?.map((source, index) => {
                        const label = source.source_name || source.source_id || source.category || `知识条目 #${source.document_id}`;
                        const chunk = source.chunk_number && source.chunk_count
                          ? ` · 分块 ${source.chunk_number}/${source.chunk_count}`
                          : "";
                        return (
                          <div className="citation-item" key={`${source.source_id || source.document_id}-${source.chunk_number || index}`}>
                            <Tag color="blue" bordered={false}>{index + 1}</Tag>
                            <span>
                              <strong>{label}</strong>
                              {source.title ? ` · ${source.title}` : ""}
                              {chunk}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {item.role === "assistant" && item.content && !item.pending && item.id !== "welcome" && (
                  <Space size={4} className="message-actions">
                    <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => void copyMessage(item.content)}>复制</Button>
                  </Space>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="suggestion-row">
          {suggestions.map((item) => (
            <Button key={item.text} icon={item.icon} onClick={() => void sendMessage(item.text)}>
              {item.text}
            </Button>
          ))}
        </div>

        <div className="composer">
          {error && <Alert closable type="error" message={error} onClose={() => setError(undefined)} />}
          <Input.TextArea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder="描述你的服务需求、日期、时间或偏好……"
          />
          <Space>
            <Typography.Text type="secondary" className="composer-tip">Enter 发送 · Shift + Enter 换行</Typography.Text>
            {!streaming && messages.some((item) => item.role === "user") && (
              <Button type="text" icon={<RedoOutlined />} onClick={askAgain}>再次提问</Button>
            )}
            {streaming ? (
              <Button danger icon={<StopOutlined />} onClick={stop}>停止</Button>
            ) : (
              <Button type="primary" shape="circle" size="large" icon={<ArrowUpOutlined />} onClick={() => void sendMessage()} />
            )}
          </Space>
        </div>
      </Card>
    </div>
  );
}
