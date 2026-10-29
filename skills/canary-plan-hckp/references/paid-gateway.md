# 付费链路说明（402 / Payment-Needed / Payment-Proof）

本技能的核心专业规范包由远端服务按次提供，遵循支付宝 **AI 按量付费**（A2A）协议：
基于 HTTP `402 Payment Required`，通过 `Payment-Needed` 响应头下发账单、
`Payment-Proof` 请求头回传支付凭证。协议原文见 <https://aipay.alipay.com/docs/ai-receive/MACHINE_PAY.html>。

> 本文件只描述契约与联调方法，**不含任何真实密钥、账号或服务地址**。
> 生产配置由部署方在服务端环境变量中提供。

## 一、角色分工

| 角色 | 负责 | 不负责 |
|------|------|--------|
| 远端服务（卖家后端） | 生成账单、验付、交付资源、履约回执 | 不处理用户界面 |
| 本技能的编排脚本 | 发起请求、调起官方支付能力、展示结果 | **不接触支付密钥、不自行实现支付与签名** |
| 官方支付能力（买家侧 `@alipay/agent-payment`） | 展示账单、引导付款、回传凭证 | — |

## 二、资源接口契约

| 项 | 值 |
|----|----|
| 方法 | `POST` |
| 单价 | `1.00`（元）/ 次 |
| 成功响应 | `200` + 资源 JSON（位于 `data` 字段） |

未配置/未部署时，脚本会以清晰错误退出，不会静默降级。

## 三、三步服务端逻辑

### 第 1 步：未付款 → 返回 402 账单

收到资源请求时先检查 `Payment-Proof`：缺失或解析失败 → 返回 `402`，
并在 `Payment-Needed` 响应头放入 **Base64URL** 编码的账单。

```
HTTP/1.1 402 Payment Required
Payment-Needed: <base64url 编码后的账单 JSON>
```

账单解码后的结构：

```json
{
  "protocol": {
    "out_trade_no": "<商户订单号，全局唯一，用于幂等>",
    "amount": "1.00",
    "currency": "CNY",
    "resource_id": "<本技能资源路径>",
    "pay_before": "<ISO8601 支付截止时间>",
    "seller_signature": "<RSA2 签名>",
    "seller_sign_type": "RSA2",
    "seller_unique_id": "<seller_id>"
  },
  "method": {
    "seller_name": "<收款方名称>",
    "seller_id": "<收款方 2088ID>",
    "seller_app_id": "<商户应用 app_id>",
    "goods_name": "<付费资产名>",
    "seller_unique_id_key": "seller_id",
    "service_id": "<服务 ID>"
  }
}
```

**加签规则**：用应用私钥对下列 8 个字段按 key **字典序**排序后拼成 `k=v&k=v`
的字符串做 RSA2(SHA256) 签名，仅本地计算、不请求支付宝：

`amount`、`currency`、`goods_name`、`out_trade_no`、`pay_before`、`resource_id`、
`seller_id`、`service_id`

### 第 2 步：携带 Payment-Proof 重试 → 验付

`Payment-Proof` 是 Base64 编码的凭证，解码后：

```json
{
  "protocol": { "payment_proof": "<支付凭证>", "trade_no": "<支付宝订单号>" },
  "method":   { "client_session": "<买家客户端会话标识>" }
}
```

服务端调 `alipay.aipay.agent.payment.verify` 核验，并**逐项检查业务字段**，
任一不通过即再返回 402：

| 检查项 | 要求 |
|--------|------|
| `active` | 必须为 `true` |
| `amount` | 必须等于资源价格 `1.00` |
| `out_trade_no` | 必须等于本服务生成的商户订单号 |
| `resource_id` | 必须与本次访问的资源一致 |
| `trade_no` | 不得重复履约（防同一笔支付被多次使用） |

### 第 3 步：交付资源 → 履约回执

返回资源后**异步**调 `alipay.aipay.agent.fulfillment.confirm`，入参为 `trade_no`。
回执失败可重试，保持幂等；**不得因回执失败要求用户重新付款**。

## 四、订单与幂等

- 订单状态可恢复：进程重启后仍能查到同一 `out_trade_no` 的支付与履约状态。
- 同一订单重复请求不重复扣费、不重复交付；已支付过的凭证再次请求会直接重放资源。

## 五、本地联调（不产生真实扣款）

服务端部署前，可用桩服务把链路跑通（桩服务不请求支付宝）：

```bash
# 1) 起本地桩服务
AIPAY_STUB=1 python3 <服务端目录>/server.py --host 127.0.0.1 --port 8787

# 2) 指向本地桩服务
export PAY_API_BASE="http://127.0.0.1:8787"

# 3) probe：应取得 402 账单并落盘
python3 scripts/pay_client.py probe --state-dir .state/local
```

联调要覆盖的分支：`402 正常`、`支付处理中`、`成功`、`失败`、`同订单重试`、`凭证缺失/过期`。
真实扣款验证需在服务端上线后进行，且买家账号须与卖家账号不同。

## 六、发布前自查（官方口径）

| 自查项 | 要求 |
|--------|------|
| 402 账单 | 未付款时返回 402 与 `Payment-Needed` |
| 凭证重试 | 支付后携 `Payment-Proof` 重试，原请求参数保持一致 |
| 服务端验付 | 验证凭证与订单；无效或过期时不交付资源 |
| 商家履约 | 返回资源后发送履约确认，失败可重试 |
| 订单与幂等 | 订单状态可恢复，不重复扣费或交付 |

价格三处必须一致：**支付宝登记价 = 服务端账单价 = 平台展示价**。
