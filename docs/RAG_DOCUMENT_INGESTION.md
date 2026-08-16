# RAG 文档导入与分块指南

项目支持把 UTF-8 编码的 Markdown 或 TXT 文档导入知识库。导入时会按中文语义边界分块、生成 Embedding、写入 SQLite，并重建当前配置的 Dense 或 Hybrid 索引。

## 支持能力

- 文件类型：`.md`、`.markdown`、`.txt`
- 单文件大小：不超过 2 MB
- 默认分块大小：500 个字符
- 默认重叠大小：100 个字符
- 单文件最多：200 个分块
- 去重方式：规范化分块正文后计算 SHA-256
- 版本同步：同一来源的新版本原子停用已经移除的旧分块
- 索引一致性：数据库记录内容代数，各服务进程在查询前惰性追平
- 分块顺序：Markdown 标题 → 段落 → 换行 → 中文句末符号 → 字符级回退
- 元数据：来源编号、文件名、章节标题、分块序号、分块总数、内容哈希

## 从管理页面导入

1. 启动项目并访问 `http://127.0.0.1:8000/knowledge`。
2. 打开“文档导入”标签页。
3. 选择 Markdown 或 TXT 文件，填写分类。
4. 首次导入可留空来源编号；更新已有文档时，从下拉建议中选择原 `source_id`。
5. 保持默认的 `500 / 100`，或按文档结构调整分块大小和重叠。
6. 导入完成后，在“知识管理”中按来源文档筛选和查看分块。

重复上传相同内容时，已经存在的分块会被跳过，不会重复消耗数据库空间。若文档会产生超过 200 个分块，请提高分块大小或先拆成多个主题文件。

## Markdown front matter

Markdown 可以在文件开头携带简单的 YAML front matter：

```yaml
---
source_id: KB-SERVICE-001
title: 服务项目、标准价格与时长说明
category: 服务项目
keywords: [价格, 时长, 项目编码]
version: 1.0
---
```

这些字段会作为元数据保存，不会进入正文分块。当前支持扁平的键值和方括号关键词列表，不支持嵌套 YAML。`source_id` 是文档的稳定身份：再次导入相同 `source_id` 时会在单个事务中保留未变化的分块、写入新分块，并停用本版已经删除的旧分块。需要持续更新的文档应显式填写它，TXT 可在管理页或 API 表单中填写。未填写时系统按“文件名 + 正文”生成内容寻址编号；这能避免两个同名但无关的文件互相覆盖，但修改后的文件会作为新来源保留，不会自动替换旧版。

## 通过 API 导入

接口为 `POST /api/knowledge/import`，请求类型是 `multipart/form-data`，字段包括：

| 字段 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `file` | 是 | 无 | Markdown 或 TXT 文件 |
| `category` | 否 | `general` | 知识分类；使用 `general` 时可由 front matter 覆盖 |
| `source_id` | 否 | 内容寻址 ID | 稳定来源编号；更新同一文档时沿用原值 |
| `chunk_size` | 否 | `500` | 100～4000 |
| `chunk_overlap` | 否 | `100` | 不小于 0，且不能超过 `chunk_size` 的 50% |

PowerShell 示例：

```powershell
$form = @{
    file = Get-Item '.\knowledge_documents\01_service_price_and_duration.md'
    category = 'general'
    source_id = 'KB-SERVICE-001'
    chunk_size = '500'
    chunk_overlap = '100'
}
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/knowledge/import' -Method Post -Form $form
```

## 导入演示知识库

仓库的 `knowledge_documents/` 包含 10 篇专门用于展示 RAG 差异的中文长文。完成模型和 Embedding 配置后运行：

```powershell
.\.venv\Scripts\python.exe scripts\import_demo_knowledge.py
```

脚本重复运行是安全的：相同分块会被哈希去重；即使只修改标题、分类、关键词或版本号，也会在批量同步结束后刷新索引。

## 对比评估

离线 Whole-document 与 Chunked BM25 对比不访问模型 API：

```powershell
.\.venv\Scripts\python.exe evaluation\eval_document_chunking.py
```

脚本输出 Evidence@1、Evidence@3、Evidence@10、MRR、具体 Top-3 候选和上下文字符数。只有候选同时来自正确文档且包含预期证据片段才算命中，可用来观察整篇文档索引与分块索引在精确编号、跨章节规则、相似干扰和上下文成本上的差异。

导入真实知识库后，还可以继续运行现有的 Dense、Hybrid 和 Rerank 对比：

```powershell
.\.venv\Scripts\python.exe -m evaluation.eval_retrieval_compare
.\.venv\Scripts\python.exe -m evaluation.eval_rerank_compare
```

演示文档是合成业务资料，不构成医疗建议或真实门店承诺。
