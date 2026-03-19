# 钉钉机器人接入指南

本文档说明如何将 OpenClaw 科研智能体接入钉钉，实现与飞书完全对等的对话能力。

## 前置条件

- OpenClaw 已部署并正常运行
- 拥有钉钉**企业管理员**或**开发者**权限

## 步骤一：创建钉钉应用

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com/)
2. 进入「应用开发」→「企业内部开发」→「创建应用」
3. 填写应用名称（如 `OpenClaw 科研助手`）和描述

## 步骤二：添加机器人能力

1. 在应用管理页面，进入「添加应用能力」
2. 选择「机器人」→ 点击「添加」
3. 填写机器人信息：
   - 机器人名称：`OpenClaw 科研助手`
   - 消息接收模式：**选择 Stream 模式**（重要！无需配置公网 IP）
   - 其他字段按需填写

## 步骤三：获取凭证

在应用管理页面获取以下三项信息：

| 配置项 | 获取位置 |
|--------|---------|
| **ClientID (AppKey)** | 应用信息 → 凭证与基础信息 |
| **ClientSecret (AppSecret)** | 应用信息 → 凭证与基础信息 |
| **robotCode** | 机器人管理页面 → 机器人配置 |

## 步骤四：开通权限

在应用管理页面 →「权限管理」中开通以下权限：

- `企业内机器人发送消息` — 用于主动推送（如科研日报）
- `消息通知` — 用于接收用户消息

## 步骤五：配置 OpenClaw

编辑项目根目录的 `.env` 文件，添加钉钉配置：

```bash
# 钉钉机器人配置
DINGTALK_APP_KEY=你的ClientID
DINGTALK_APP_SECRET=你的ClientSecret
DINGTALK_ROBOT_CODE=你的robotCode
```

## 步骤六：安装依赖并重启

```bash
# 安装钉钉 SDK
conda run -n claw pip install dingtalk-stream>=0.24.0

# 重启服务
./openclaw restart
```

## 步骤七：验证

1. 查看启动日志，确认出现：
   ```
   ✅ Dingtalk Bot 启动
   ```

2. 在钉钉中找到机器人，发送测试消息：
   - **私聊**：直接发送，如「介绍下 Transformer 的注意力机制」
   - **群聊**：需要 @机器人，如「@OpenClaw科研助手 帮我查一下最新的 AI 论文」

3. 预期响应流程：
   - 立即收到「🤔 已收到，正在为您处理，请稍候…」
   - 稍后收到 Markdown 格式的回复卡片，包含追问建议

## 功能说明

钉钉渠道与飞书完全对等，支持：

| 功能 | 说明 |
|------|------|
| 对话问答 | 私聊/群聊 @机器人 均可 |
| 卡片回复 | Markdown 富文本 + 追问建议 + Agent 元信息 |
| 用户画像 | 自动提取兴趣领域和关键词 |
| 前沿日报 | 每日定时推送个性化科研前沿（需配置 DINGTALK_ROBOT_CODE） |

## 架构说明

OpenClaw 采用 **Channel Adapter 架构**，每个 IM 渠道是一个独立的适配器：

```
channels/
├── base.py              # 抽象接口 ChannelAdapter
├── feishu_adapter.py    # 飞书适配器
└── dingtalk_adapter.py  # 钉钉适配器
```

未来接入新渠道（如 Slack、微信、Telegram）只需新增一个 adapter 文件，无需修改核心代码。

## 故障排查

| 问题 | 排查方向 |
|------|---------|
| 启动日志无 DingTalk 相关输出 | 检查 `.env` 中 `DINGTALK_APP_KEY` 和 `DINGTALK_APP_SECRET` 是否已填写 |
| `dingtalk-stream 未安装` | 运行 `pip install dingtalk-stream` |
| 发消息无响应 | 检查应用是否已发布、机器人消息接收模式是否选择了 Stream 模式 |
| 主动推送失败 | 检查 `DINGTALK_ROBOT_CODE` 是否配置、权限是否开通 |
