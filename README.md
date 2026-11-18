# 采购三方勾稽报告

面向真实数据复核的中型数据工程工具，包含六个可独立调用的业务模块。规则、失败原因与证据字段见 docs/DATA-DICTIONARY.md，陷阱和测试映射见 docs/DEPTH.md。

## 启动

docker compose up --build

浏览器访问 http://localhost:18218。测试：docker compose exec -T app pytest -q

## 模块

- M1 单据采信：页面 /documents，接口 /api/documents/import
- M2 主数据映射：页面 /master，接口 /api/master/resolve
- M3 数量勾稽：页面 /quantity，接口 /api/quantity/match
- M4 税额核验：页面 /tax，接口 /api/tax/check
- M5 多对多匹配：页面 /matching，接口 /api/matches/solve
- M6 差异批次：页面 /batches，接口 /api/reconcile

## 运行规则

同一输入内容生成相同运行标识，重复执行复用已有批次。隔离记录包含原因码、原始行号与原值；业务导出只保留一份证据清单。

## 匹配与批次

- 已核准配对稳定优先；默认 `policy="settle"` 下，若旧票另有一组等额验收单，
  会挪配让新旧两张票都结算；没有替代组时保留原配对，新票以
  `RECEIPT_HELD_BY_APPROVED` 挂起。传 `policy="stable"` 则始终保留原配对。
- 验收单在同批次内互斥，一张验收单不会算给两张发票。
- M1 采信结果决定匹配边界：隔离行不参与组合。
- 差异批次按实际配对金额对账，页面列出实际配对、待处理原因与未开票验收单。
- 同批重跑（相同输入字节）复用 run_id，不重复占用验收单。

## 接口载荷

```json
{
  "documents": [
    {"doc_type": "GR", "doc_no": "GR-1", "sku": "000142", "qty": "10",
     "amount": "1,000.00", "tax_rate": "13%"}
  ],
  "receipts": [],
  "invoices": [],
  "approved": [{"invoice_id": "IV-1", "receipt_ids": ["GR-1"]}],
  "policy": "settle",
  "tolerance": "0.01",
  "limit": 4
}
```

`documents` 走 M1 采信与 M2 映射；`receipts`/`invoices` 是已采信记录的显式入口。
