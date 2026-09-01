import { useEffect, useMemo, useState } from "react";
import {
  CloudUploadOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SyncOutlined,
  SearchOutlined
} from "@ant-design/icons";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
  message
} from "antd";
import type { UploadFile } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage } from "../api";
import PageTitle from "../components/PageTitle";
import PageError from "../components/PageError";
import type { KnowledgeDocument } from "../types";

interface KnowledgeFormValues {
  content: string;
  category: string;
  keywords?: string;
}

interface ImportFormValues {
  category: string;
  source_id?: string;
  chunk_size: number;
  chunk_overlap: number;
}

function keywordArray(value?: string[] | string) {
  if (Array.isArray(value)) return value;
  return value ? value.split(/[,，]/).map((item) => item.trim()).filter(Boolean) : [];
}

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>();
  const [editorOpen, setEditorOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeDocument>();
  const [saving, setSaving] = useState(false);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<KnowledgeFormValues>();
  const [importForm] = Form.useForm<ImportFormValues>();
  const queryClient = useQueryClient();
  const knowledgeQuery = useQuery({
    queryKey: ["knowledge"],
    queryFn: api.listKnowledge
  });
  const lifecycleQuery = useQuery({
    queryKey: ["knowledge-lifecycle"],
    queryFn: api.knowledgeLifecycle
  });

  const backup = async () => {
    try {
      const result = await api.backupKnowledge();
      message.success(`备份完成：${result.data.path}`);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const syncBaseline = () => {
    Modal.confirm({
      title: "同步受管知识文档？",
      content: "系统会先备份 SQLite，再增量同步 knowledge_documents 并仅在有变化时重建索引。",
      okText: "备份并同步",
      cancelText: "取消",
      onOk: async () => {
        try {
          const result = await api.syncManagedKnowledge();
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
            queryClient.invalidateQueries({ queryKey: ["knowledge-lifecycle"] })
          ]);
          message.success(`同步完成：变更 ${result.data.totals.changed || 0} 个分块`);
          await load();
        } catch (error) {
          message.error(getErrorMessage(error));
          throw error;
        }
      }
    });
  };

  const load = async () => {
    const result = await knowledgeQuery.refetch();
    if (result.data) {
      setDocuments(result.data.documents || []);
      setCategories(result.data.categories || []);
    }
  };

  useEffect(() => {
    if (knowledgeQuery.data) {
      const result = knowledgeQuery.data;
      setDocuments(result.documents || []);
      setCategories(result.categories || []);
    }
  }, [knowledgeQuery.data]);

  const runSearch = async () => {
    if (!query.trim()) {
      await load();
      return;
    }
    setSearching(true);
    try {
      const result = await api.searchKnowledge(query.trim(), category);
      setDocuments(result.results || []);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSearching(false);
    }
  };

  const openEditor = (document?: KnowledgeDocument) => {
    setEditing(document);
    form.setFieldsValue({
      content: document?.content || "",
      category: document?.category || categories[0] || "general",
      keywords: keywordArray(document?.keywords).join("，")
    });
    setEditorOpen(true);
  };

  const save = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const payload = {
        content: values.content,
        category: values.category,
        keywords: keywordArray(values.keywords)
      };
      if (editing) await api.updateKnowledge(editing.id, payload);
      else await api.createKnowledge(payload);
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      message.success(editing ? "知识条目已更新" : "知识条目已新增");
      setEditorOpen(false);
      await load();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: number) => {
    try {
      await api.deleteKnowledge(id);
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      message.success("知识条目已删除");
      await load();
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const importDocument = async () => {
    const values = await importForm.validateFields();
    const file = fileList[0]?.originFileObj;
    if (!file) {
      message.warning("请选择 Markdown 或 TXT 文件");
      return;
    }
    setSaving(true);
    try {
      const data = new FormData();
      data.append("file", file);
      data.append("category", values.category);
      data.append("source_id", values.source_id || "");
      data.append("chunk_size", String(values.chunk_size));
      data.append("chunk_overlap", String(values.chunk_overlap));
      const result = await api.importKnowledge(data);
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      message.success(result.message || "文档导入完成");
      setImportOpen(false);
      setFileList([]);
      await load();
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const columns = useMemo<ColumnsType<KnowledgeDocument>>(() => [
    {
      title: "知识内容",
      dataIndex: "content",
      render: (value: string, record) => (
        <div className="knowledge-content">
          <Typography.Text strong>{value || "未命名知识"}</Typography.Text>
          {(record.source || record.section) && (
            <Typography.Text type="secondary">{[record.source, record.section].filter(Boolean).join(" · ")}</Typography.Text>
          )}
        </div>
      )
    },
    { title: "分类", dataIndex: "category", width: 150, render: (value: string) => <Tag color="geekblue">{value}</Tag> },
    {
      title: "关键词",
      dataIndex: "keywords",
      width: 220,
      render: (value: string[] | string | undefined) => keywordArray(value).slice(0, 3).map((item) => <Tag key={item}>{item}</Tag>)
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_, record) => (
        <Space>
          <Button type="text" icon={<EditOutlined />} onClick={() => openEditor(record)} />
          <Popconfirm title="确认删除这条知识？" onConfirm={() => void remove(record.id)}>
            <Button type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ], [categories]);

  if (knowledgeQuery.isError && documents.length === 0) {
    return <PageError title="知识库加载失败" description={getErrorMessage(knowledgeQuery.error)} onRetry={() => void load()} />;
  }

  return (
    <div>
      <PageTitle
        eyebrow="RAG KNOWLEDGE"
        title="知识库管理"
        description="维护咨询知识、导入长文档，并通过语义搜索验证召回结果。"
        action={<Space><Button icon={<CloudUploadOutlined />} onClick={() => setImportOpen(true)}>导入文档</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>新增知识</Button></Space>}
      />

      <Row gutter={[16, 16]} className="stats-row">
        <Col xs={24} sm={8}><Card><Statistic title="知识条目" value={documents.length} prefix={<FileTextOutlined />} /></Card></Col>
        <Col xs={24} sm={8}><Card><Statistic title="知识分类" value={categories.length} /></Card></Col>
        <Col xs={24} sm={8}><Card><Statistic title="检索模式" value="Hybrid RAG" valueStyle={{ fontSize: 22 }} /></Card></Col>
      </Row>

      <Card className="data-card lifecycle-card">
        <div className="table-toolbar">
          <Space direction="vertical" size={2}>
            <Typography.Text strong><SafetyCertificateOutlined /> 知识库基线</Typography.Text>
            <Typography.Text type="secondary">
              源文档 {lifecycleQuery.data?.data.source.document_count ?? "--"} 篇 / {lifecycleQuery.data?.data.source.chunk_count ?? "--"} 个受管分块 · 数据库活跃 {lifecycleQuery.data?.data.database.active_chunk_count ?? "--"} 条 · 清单
              <Tag color={lifecycleQuery.data?.data.manifest.matches ? "success" : "warning"}>{lifecycleQuery.data?.data.manifest.status || "loading"}</Tag>
              数据库<Tag color={lifecycleQuery.data?.data.database.matches ? "success" : "warning"}>{lifecycleQuery.data?.data.database.status || "loading"}</Tag>
            </Typography.Text>
          </Space>
          <Space>
            <Button onClick={() => void backup()}>创建快照</Button>
            <Button type="primary" icon={<SyncOutlined />} disabled={!lifecycleQuery.data?.data.manifest.matches} onClick={syncBaseline}>备份并同步</Button>
          </Space>
        </div>
      </Card>

      <Card className="data-card">
        <div className="table-toolbar">
          <Space wrap>
            <Input
              allowClear
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onPressEnter={() => void runSearch()}
              prefix={<SearchOutlined />}
              placeholder="语义搜索知识内容"
              style={{ width: 280 }}
            />
            <Select allowClear value={category} onChange={setCategory} placeholder="全部分类" options={categories.map((item) => ({ label: item, value: item }))} style={{ width: 160 }} />
            <Button type="primary" onClick={() => void runSearch()}>搜索</Button>
          </Space>
          <Button loading={knowledgeQuery.isFetching} icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        </div>
        <Table rowKey="id" loading={knowledgeQuery.isPending || searching} columns={columns} dataSource={documents} pagination={{ pageSize: 8, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} />
      </Card>

      <Modal title={editing ? "编辑知识" : "新增知识"} open={editorOpen} onCancel={() => setEditorOpen(false)} onOk={() => void save()} confirmLoading={saving} width={680}>
        <Form form={form} layout="vertical">
          <Form.Item name="content" label="知识内容" rules={[{ required: true, message: "请输入知识内容" }]}><Input.TextArea rows={7} placeholder="输入可供 AI 检索和回答的知识内容" /></Form.Item>
          <Row gutter={16}>
            <Col span={10}><Form.Item name="category" label="分类" rules={[{ required: true }]}><Select showSearch options={categories.map((item) => ({ label: item, value: item }))} /></Form.Item></Col>
            <Col span={14}><Form.Item name="keywords" label="关键词"><Input placeholder="多个关键词使用逗号分隔" /></Form.Item></Col>
          </Row>
        </Form>
      </Modal>

      <Modal title="导入知识文档" open={importOpen} onCancel={() => setImportOpen(false)} onOk={() => void importDocument()} confirmLoading={saving}>
        <Form form={importForm} layout="vertical" initialValues={{ category: "general", chunk_size: 500, chunk_overlap: 100 }}>
          <Form.Item label="文档" required><Upload.Dragger accept=".md,.txt" maxCount={1} fileList={fileList} beforeUpload={() => false} onChange={({ fileList: next }) => setFileList(next)}><p className="ant-upload-drag-icon"><CloudUploadOutlined /></p><p>点击或拖拽 Markdown / TXT 文件到这里</p><p className="ant-upload-hint">单个文件不超过 2 MB</p></Upload.Dragger></Form.Item>
          <Form.Item name="category" label="分类" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="source_id" label="来源标识"><Input placeholder="留空时自动生成" /></Form.Item>
          <Row gutter={16}>
            <Col span={12}><Form.Item name="chunk_size" label="分块大小"><InputNumber min={100} max={2000} style={{ width: "100%" }} /></Form.Item></Col>
            <Col span={12}><Form.Item name="chunk_overlap" label="重叠长度"><InputNumber min={0} max={500} style={{ width: "100%" }} /></Form.Item></Col>
          </Row>
        </Form>
      </Modal>
    </div>
  );
}
