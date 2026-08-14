# RAG 演示知识文档

本目录是一组用于演示文档导入、中文分块、混合检索和精排的合成门店知识。内容与项目默认知识保持一致，但不代表真实医疗建议或现实门店承诺。

## 文档设计

| 来源编号 | 主题 | 主要演示点 |
| --- | --- | --- |
| KB-SERVICE-001 | 服务价格与时长 | 精确数字、项目编码，适合展示 BM25 |
| KB-SERVICE-002 | 服务体验与适用场景 | 口语化问题与规范描述，适合展示 Dense |
| KB-SAFETY-003 | 服务边界与注意事项 | 与“效果”文档形成相似干扰，适合展示 Rerank |
| KB-MEMBER-004 | 会员与储值 | 精确档位、权益编码，适合展示 BM25 |
| KB-BOOKING-005 | 预约、改期与取消 | 跨章节规则和时间数字，适合展示 Chunking |
| KB-TECH-006 | 技师能力目录 | 高度相似条目中的工号检索，适合展示 BM25 |
| KB-STORE-007 | 营业、地址与到店 | 地址、地铁口、设施编码，适合展示精确检索 |
| KB-AFTERCARE-008 | 服务后反馈与客诉 | 流程编号和跨章节处理方式 |
| KB-GUIDE-009 | 场景化项目选择 | 大量语义改写，适合展示 Dense |
| KB-FAQ-010 | 综合问答与边界说明 | 相似问题干扰与跨段检索 |

所有文件都使用 YAML front matter 保存 `source_id`、标题、分类、关键词和版本。建议以 500 个中文字符左右、100 个字符重叠导入；具体分块数由分块器按标题、段落和句子边界决定。

## 一键导入与评估

先配置项目模型与 Embedding 环境，再运行：

```powershell
.venv\Scripts\python.exe scripts\import_demo_knowledge.py
```

如果只想核对预计分块数量，不访问数据库或 Embedding API：

```powershell
.venv\Scripts\python.exe scripts\import_demo_knowledge.py --dry-run
```

仅做离线分块检索对比（不访问数据库和模型 API）：

```powershell
.venv\Scripts\python.exe evaluation\eval_document_chunking.py
```

评估脚本报告 Whole document 与 Chunked passage 两种索引的 Evidence Recall@1、@3、@10 和 Evidence MRR。只有返回的具体候选同时属于正确来源且包含预期证据片段才算命中；仅召回同来源的其他分块不计入核心指标。报告还会显示来源排名、具体的 Top-3 `source_id#chunk_index`、平均候选长度与 Top-3 上下文字符数，方便定位“来源对了但片段错了”的问题。

这项离线实验只使用 BM25，不应宣称分块必然提高 Top-1 或 MRR。整篇文档的候选数量少，来源排名可能更高；分块后的主要收益是把答案定位到更短的 passage、减少交给 LLM 的无关上下文，并为后续 Dense/Hybrid/Rerank 提供候选池。应结合 Evidence@10 和上下文压缩率解读，而不是只挑一个有利指标。

当前 17 条固定用例、默认 500/100 参数的可重复基线为：Whole 的 Evidence@1/@3/@10 是 88.2%/100%/100%，MRR 为 0.941；Chunked 是 76.5%/100%/100%，MRR 为 0.863。Chunked 的平均候选长度由 976.5 字符降至 114.5 字符，平均 Top-3 上下文由 2985.5 字符降至 389.5 字符，压缩约 87.0%。这说明原始 BM25 Top-1 存在粒度权衡，但正确证据仍完整进入 Top-10 候选池，适合交给后续精排；其中跨章节分组的 Chunked Evidence@1 为 100%。
