# 深度登记 采购三方勾稽报告

- 规模：中型
- 核心规则：采购、分批验收和合并发票按货号与税率分组，数量和金额使用 Decimal 守恒。
- 使用者与工作流：应付会计导入三类单据，映射主数据，核对数量税额，多对多匹配并导出差异。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 单据采信 | 导入三类单据并隔离冲突 | POST /api/documents/import | `app/modules/m1_documents/parser.py` `app/modules/m1_documents/money.py` | `tests/test_m1_documents.py` |
| M2 主数据映射 | 归一货号与旧字段 | POST /api/master/resolve | `app/modules/m2_master/aliases.py` `app/modules/m2_master/evolve.py` | `tests/test_m2_master.py` |
| M3 数量勾稽 | 统一包装单位并核对未到量 | POST /api/quantity/match | `app/modules/m3_quantity/units.py` `app/modules/m3_quantity/match.py` | `tests/test_m3_quantity.py` |
| M4 税额核验 | 重算净额、税额与含税额 | POST /api/tax/check | `app/modules/m4_tax/calculate.py` `app/modules/m4_tax/verify.py` | `tests/test_m4_tax.py` |
| M5 多对多匹配 | 在相同货号税率内组合匹配 | POST /api/matches/solve | `app/modules/m5_matching/candidates.py` `app/modules/m5_matching/solver.py` | `tests/test_m5_matching.py` |
| M6 差异批次 | 核对金额守恒并导出差异 | POST /api/reconcile | `app/modules/m6_batches/compare.py` `app/modules/m6_batches/exporter.py` | `tests/test_m6_batches.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-05 | 单据缺少字段 | 静默补空进入匹配 | 隔离并保留行号 | `app/modules/m1_documents/parser.py` | `tests/test_m1_documents.py::test_missing_field_quarantined` |
| TRAP-06 | 金额带千分位 | 直接转换失败 | 清理分隔符后使用 Decimal | `app/modules/m1_documents/money.py` | `tests/test_m1_documents.py::test_thousands_amount_uses_decimal` |
| TRAP-07 | 空值哨兵 | 被当作零金额 | 空值集合统一拒绝 | `app/modules/m1_documents/money.py` | `tests/test_m1_documents.py::test_missing_field_quarantined` |
| TRAP-08 | 供应商编码前导零 | 转整数后主数据失配 | 编码始终按字符串保形 | `app/modules/m2_master/aliases.py` | `tests/test_m2_master.py::test_supplier_code_preserves_leading_zero` |
| TRAP-10 | 税额浮点尾差 | 分项和总额差一分 | Decimal 半入到分 | `app/modules/m4_tax/calculate.py` | `tests/test_m4_tax.py::test_half_up_to_cent` |
| TRAP-14 | 重复单号值冲突 | 后写覆盖金额 | 单独隔离冲突版本 | `app/modules/m1_documents/parser.py` | `tests/test_m1_documents.py::test_conflicting_duplicate_detected` |
| TRAP-17 | 旧列名仍在使用 | 新版读取报错 | 受控字段迁移并报告冲突 | `app/modules/m2_master/evolve.py` | `tests/test_m2_master.py::test_old_columns_migrate` |
| TRAP-19 | 组合候选顺序变化 | 匹配结果随输入顺序变化 | 按差额、数量与编号稳定择一 | `app/modules/m5_matching/solver.py` | `tests/test_m5_matching.py::test_choice_is_order_independent` |
| TRAP-23 | 相邻税率边界 | 不同税率金额互相抵消 | 货号与税率共同分组 | `app/modules/m4_tax/verify.py` | `tests/test_m4_tax.py::test_different_tax_rates_never_offset` |
| TRAP-26 | 同批重复执行 | 重复新增对账结果 | 输入摘要作为运行唯一键 | `app/store.py` | `tests/test_m6_batches.py::test_idempotent_record` |

## 对账规则

- 采购数量 = 净验收数量 + 未到数量。
- 净验收含税额 = 已开票含税额 + 未开票金额 + 已解释差异。
- 不同税率的行不可相互抵消。

## 已知不覆盖

- 不处理外币汇兑，样例与输出均为人民币。


## 匹配与批次深化（PM-2026.11）

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-27 | 已核准配对独占验收单 | 新发票永远抢不到验收单或被强行覆盖 | 默认优先多结算，必要时把旧票挪到等额替代组；无法替代时保留原配对并给出 `RECEIPT_HELD_BY_APPROVED` | `app/modules/m5_matching/allocator.py` | `tests/test_allocation.py::test_move_settles_both_when_approved_has_equal_replacements` |
| TRAP-28 | 同一张验收单被两张发票使用 | 金额重复计入 | 分配集合互斥，`receipt_usage` 校验重复占用 | `app/modules/m5_matching/allocator.py` `app/modules/m6_batches/compare.py` | `tests/test_allocation.py::test_receipt_is_never_shared_between_invoices` |
| TRAP-29 | 隔离行偷偷进入匹配 | 冲突单据也参与组合 | 只使用 M1 `accepted` 行，隔离行保留原因码与原始行号 | `app/pipeline.py` | `tests/test_allocation.py::test_quarantined_document_never_enters_matching` |
| TRAP-30 | 批次按发票面值对账 | 配对金额与账面不符仍然平衡 | 差异批次按实际配对金额（验收单金额合计）核对 | `app/modules/m6_batches/compare.py` | `tests/test_allocation.py::test_reconcile_uses_actual_pairing_amounts` |
| TRAP-31 | 同批重跑重复占用验收单 | 每次运行都新增配对 | 输入摘要作为 run_id，重跑复用批次与配对明细 | `app/store.py` `app/modules/m6_batches/service.py` | `tests/test_allocation.py::test_batch_rerun_reuses_allocations` |

策略：`policy="settle"`（默认）在“已核准发票仍结算”的前提下最大化结算张数，再最大化原样保留的核准配对；`policy="stable"` 把核准配对当硬约束，新票只能使用剩余验收单。
