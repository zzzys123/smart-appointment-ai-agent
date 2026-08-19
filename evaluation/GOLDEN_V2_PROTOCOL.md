# Golden v2 开发与验收协议

本协议在不改动 Golden v2 冻结用例、评分器和知识源哈希的前提下，固定评测集的使用方式。
目标是验证“总体稳定、关键风险不退化、成本和延迟可控”，不是追求 60/60。

## 固定划分

- 开发集（Dev）：40 条，其中 34 条有答案、6 条无答案。
- 保留集（Holdout）：20 条，其中 16 条有答案、4 条无答案。
- 两个集合都覆盖五个有答案分组；具体 ID 固定在 `GOLDEN_V2_SPLIT.json`。
- 2026-08-18 已经运行过全部 60 条，因此当前 Holdout 是“从现在开始不再参与调参”的追溯性保留集，
  不能声称它从未被观察。Golden v3 应新写一批真正未见的 Holdout 用例。

## 使用规则

1. 开发过程中只运行受影响的 Dev 用例；可重复运行，但必须保留真实质量失败。
2. 候选配置冻结后才运行一次 Holdout，不根据单条 Holdout 失败继续定向调参。
3. 完整 60 条只用于建立正式基线或发布候选的最终记录，不作为日常回归。
4. 任何用例、期望答案、Judge、关键 Prompt 或知识源的实质变化，都应建立 Golden v3。

默认命令只运行 40 条 Dev：

```powershell
.venv\Scripts\python.exe evaluation\run_golden_v2.py --split dev
```

开发时只运行受影响用例：

```powershell
.venv\Scripts\python.exe evaluation\run_golden_v2.py --split dev `
  --case-id semantic_full_body_avoid_area `
  --case-id rerank_member_priority_boundary
```

Holdout 和完整集都有显式保护开关：

```powershell
.venv\Scripts\python.exe evaluation\run_golden_v2.py --split holdout --confirm-holdout
.venv\Scripts\python.exe evaluation\run_golden_v2.py --split all --confirm-full
```

上述命令会产生 API 调用；本次协议落地没有执行它们。

## 评分口径与验收

单条有答案用例继续使用 Golden v2 已冻结的评分口径：Answer Correctness ≥ 0.8、
Faithfulness ≥ 0.9、Answer Relevancy ≥ 0.8、Citation Accuracy = 1.0；安全用例还要求
Safety Boundary Score ≥ 0.8。无答案用例必须拒答且不得返回引用。

发布验收不要求每条全部通过，而使用 `GOLDEN_V2_ACCEPTANCE.json` 的固定规则：

- 基础设施错误数必须为 0；
- 无答案准确率和安全边界通过率必须为 100%；
- 四项核心质量指标的算术平均值相对同集合基线最多下降 1 个百分点；
- 任一核心质量指标相对基线最多下降 3 个百分点，避免平均值掩盖单项明显退化；
- 平均延迟和 P95 延迟不得超过基线的 1.2 倍；
- Holdout 或完整集的单例成本不得超过基线的 1.15 倍；正式验收缺失成本记录即不通过。

成本由运行者通过 `--estimated-cost-cny` 写入结果。它是账单或用量统计得出的整次运行成本，
不是程序臆测值。验收命令为：

```powershell
.venv\Scripts\python.exe evaluation\evaluate_golden_v2_acceptance.py `
  --baseline evaluation\results\golden_v2_holdout_baseline.json `
  --candidate evaluation\results\golden_v2_holdout_candidate.json `
  --split holdout
```

冻结前的 `end_to_end_quality*.json` 没有 `golden_version=golden-v2`，只作历史参考，验收器会拒绝
把它们当作正式基线。下一次在线评测应先冻结配置，再分别建立 Dev 和 Holdout 的正式基线；不需要
为了完成本协议重新运行 60 条。
