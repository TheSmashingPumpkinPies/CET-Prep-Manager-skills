---
name: cet-prep-manager
description: 管理大学英语四、六级备考资料，包括目标确认、真题与模考记录、写作和翻译评估、长期趋势、学习计划及 Excel 报告。涉及 CET-4、CET-6、四六级成绩或备考管理时使用。
---

# 大学英语四、六级备考管理

当前 Skill 版本：`0.2.0`。安装副本必须与仓库中的 `skill/SHA256SUMS` 完全一致。

本 Skill 负责把用户的自然语言需求转换为本地确定性命令，并以 SQLite 数据库保存长期状态。用户使用中文时，所有面向用户的说明都使用自然、通行的中文四六级术语；英文仅用于内部字段和命令。

首次执行命令前，确认 Python 包和数据库路径。环境或数据库位置不明确时，先读 [运行环境与数据库约定](references/runtime.md)。

## 必须遵守的原则

1. **先读取，再分析。** 涉及历史、进步、退步、薄弱项或计划时，先执行 `cetpm status --json`；涉及当前计划时再执行 `cetpm plan show --json`。不得用对话记忆补造数据库中不存在的成绩。
2. **确认前不建档。** 首次调用、提到四六级或自我介绍都不等于同意写入资料。新建或修改个人资料必须遵守下文的确认流程。
3. **区分四类分数。** 官方报告分、练习正确数/正确率、写作翻译的 1–15 分估分、教学诊断指标互不混用。
4. **不把练习换算成官方报告分。** 官方报告分采用常模参照，不能从一套练习题的正确率准确反推。
5. **不静默改计划。** 每次调整都生成新版本并保存原因；一次偶然波动不足以证明需要改计划。
6. **不修改安装副本。** 普通备考会话不得编辑本 Skill、参考资料、版本文件或哈希清单；发现哈希不一致时停止使用，并从可信仓库重新安装。
7. **写入后核验。** 只有命令成功并返回记录 ID，才可以告诉用户“已保存”。
8. **不自行修改 Skill。** 发现问题时向用户说明，由维护流程统一修改源仓库。
9. **遵守版权边界。** 不补写或复现缺失的真题原文；可以保存用户提供的做题成绩、错因和用户自己的答案。

## 中文术语和题型事实

回答题型、题数、分值比例、Section A/B/C 对应关系或成绩单结构前，必须先读 [四六级考试结构](references/cet-format.md) 和 [中文术语表](references/terminology-zh.md)。

- 不凭模型记忆猜题型。
- “精听、复述、听写”是训练方法，不是现行四级或六级听力题型。
- 用户只说 Section A/B/C 而考试级别不明确时，先确认是四级还是六级。
- 内部保存稳定英文标识；给用户展示时转换成术语表中的中文名称。

## 意图与工作流

### 1. 新建或修改备考资料

仅当用户明确要求建立或修改个人备考资料时进入此流程。

1. 先执行只读命令：`python -m cet_prep_manager profile show --json`。
2. 若没有资料，只询问缺失的必填项：备考四级还是六级。姓名、目标分、考试日期、每日学习时间都是可选项，用户没说就保持空白。
3. 向用户逐项展示准备保存的内容，并请求确认。
4. 只有当前对话中得到明确肯定答复后，才能执行：

```bash
python -m cet_prep_manager profile update --exam <CET4-or-CET6> [仅包含用户确认过的选项]
```

沉默、超时、含糊回答或未选择选项都不构成同意。此时不得选择默认值、不得创建资料、不得自动生成学习计划。CLI 会拒绝缺少 `--exam` 的新资料。

### 2. 记录官方考试成绩

官方成绩报告包括总分、听力、阅读、写作和翻译三个单项，其中写作和翻译在成绩报告中合并为一个单项。只保存用户从真实成绩报告中提供的数字：

```bash
python -m cet_prep_manager attempt add \
  --exam <CET4-or-CET6> --type official_exam --source-kind official_result \
  --official-score <总分> \
  --official-listening-score <听力> \
  --official-reading-score <阅读> \
  --official-writing-translation-score <写作和翻译> \
  --idempotency-key <稳定且唯一的键> --output-json
```

总分允许 0–710，听力和阅读各允许 0–249，写作和翻译允许 0–212。三项齐全时，系统会校验三项之和等于总分。用户只提供部分分数时保留空值，不猜测缺项。

### 3. 记录真题、模考或专项练习

先确认考试级别、试卷来源、日期以及用户提供的是整项成绩还是具体题型成绩。题型标识必须来自考试结构参考。

```bash
python -m cet_prep_manager attempt add --json '{
  "exam_level": "<CET4-or-CET6>",
  "attempt_type": "<full_mock-or-section_practice>",
  "source_key": "<试卷标识>",
  "source_kind": "past_paper",
  "idempotency_key": "<稳定且唯一的键>",
  "sections": [
    {
      "section": "listening",
      "subtype": "<考试结构参考中的内部标识>",
      "correct_count": "<答对数>",
      "total_count": "<题数>"
    }
  ]
}'
```

整套标准试卷会校验题数。一个模块的完整子项按官方卷面权重聚合为一次观测；如果子项不完整，只保留子项记录，不把它冒充整项趋势。若用户另有明确的整项记录，可保存 `subtype` 为空的整项数据。

### 4. 写作评估

先读 [写作与翻译评分参考](references/evaluation-rubric.md)。输出并保存：1–15 分对齐估分、估分区间、置信度、评分依据、教学诊断和具体修改建议。不得称为官方阅卷分。

```bash
python -m cet_prep_manager assessment add --json-input '{
  "section": "writing",
  "estimated_score": 11,
  "score_low": 10,
  "score_high": 12,
  "confidence": "medium",
  "rubric_version": "neea-public-v1",
  "assessor_provider": "<提供方>",
  "assessor_model": "<模型>",
  "skill_version": "<Skill版本>",
  "diagnostic": {},
  "overall_rationale": "<中文评分依据>"
}'
```

给用户的报告使用“写作估分、估分区间、评估把握度、主要问题、修改建议”等自然中文标题，不堆叠英文括注。

### 5. 翻译评估

同样使用 1–15 分对齐估分、区间和置信度。诊断重点为信息完整准确、句法结构、用词得当和表达通顺。指出中式英语、信息遗漏、时态或搭配问题，并保存结构化结果。

### 6. 状态与趋势分析

```bash
python -m cet_prep_manager status --json
```

- `insufficient`：少于 3 次，只能说数据不足。
- `emerging`：3–4 次，可以描述初步方向，但不能下稳定结论。
- `usable`：不少于 5 次，结合 MA5、趋势斜率和波动分析。
- 两次主观估分区间重叠时，不能仅凭 1–2 分差异断言进步。
- `module_balance` 只是备考诊断指标，不是 710 分换算。

### 7. 错题与学习计划

查看错题使用 `cetpm errors list --json`。只有用户明确要求、到达复盘节点、连续证据显示瓶颈或计划明显不可执行时才调整计划。计划变化必须创建新版本并记录中文原因；不得因单次成绩波动自动改计划。

### 8. 报告与导出

```bash
python -m cet_prep_manager export --output ./cet_prep_report.xlsx
python -m cet_prep_manager report weekly --output ./weekly_report.md --json
```

报告必须区分实测数据、确定性计算结果和 AI 解读。官方成绩导出时保留总分、听力、阅读、写作和翻译四列，不把练习数据放进官方成绩字段。

## 参考资料路由

- [运行环境与数据库约定](references/runtime.md)：首次运行、包或数据库路径不清楚时读取。
- [四六级考试结构](references/cet-format.md)：涉及题型、题数、分值、时间、Section 对应关系或官方成绩结构时读取。
- [中文术语表](references/terminology-zh.md)：生成中文回答、图表标题、计划或错题标签时读取。
- [写作与翻译评分参考](references/evaluation-rubric.md)：评估写作或翻译时读取。
- [版权与数据边界](references/copyright-policy.md)：用户要求复现、补全或保存真题内容时读取。
