import { FeishuSetup } from "./FeishuSetup";
import { WeixinSetup } from "./WeixinSetup";
import { DingtalkSetup } from "./DingtalkSetup";
import { WecomSetup } from "./WecomSetup";
import { FormBotSetup, type FormField, type FormFieldPreset } from "./FormBotSetup";

/**
 * Per-channel form specs for the form-paste channels (Telegram,
 * Discord, Slack, Email). Each field name matches the API's expected
 * ``platforms.<channel>.extra.<field>`` key.
 */
const TELEGRAM_FIELDS: FormField[] = [
  { name: "token", label: "Bot Token", type: "password", required: true,
    placeholder: "1234567890:ABCDEFG...",
    hint: "向 @BotFather 发送 /newbot 获取，或 /token 查看现有 bot" },
];

const DISCORD_FIELDS: FormField[] = [
  { name: "token", label: "Bot Token", type: "password", required: true,
    hint: "Discord Developer Portal → 你的 App → Bot → Token（点 Reset Token）" },
  { name: "app_id", label: "Application ID", placeholder: "可选",
    hint: "用于消息中的 Slash 命令；不用可留空" },
];

const SLACK_FIELDS: FormField[] = [
  { name: "token", label: "Bot Token", type: "password", required: true,
    placeholder: "xoxb-...",
    hint: "Slack App → OAuth & Permissions → Bot User OAuth Token" },
  { name: "signing_secret", label: "Signing Secret", type: "password",
    hint: "Slack App → Basic Information → Signing Secret（事件验签用）" },
  { name: "app_token", label: "App-Level Token", type: "password", placeholder: "xapp-...",
    hint: "可选 — Socket Mode 需要" },
];

const CUSTOMER_REPLY_PRESETS: FormFieldPreset[] = [
  {
    id: "engaged",
    label: "积极引导(默认)",
    text: "用礼貌的中文外部商务口吻回信。如果客户咨询信息不完整,在回信里一次性问全这四项:① 关注的产品/型号 ② 计划采购数量 ③ 目标交期或收货地区 ④ 目标价或参考方案。承诺\"收到信息后 1 个工作日内回详细方案\"。",
  },
  {
    id: "export",
    label: "外贸 / 英文优先",
    text: "Reply in English by default (switch to Chinese only if the inbound is clearly Chinese). Ask the client for: product / model, quantity, Incoterms preference, destination port. Include a brief one-line company intro. Promise a detailed quotation within 1 business day.",
  },
  {
    id: "industrial",
    label: "工业品 / 方案型",
    text: "用礼貌中文外部口吻回信。先问应用场景与技术参数(工况、工艺、安装环境、关键性能要求),再谈商务条款(数量、交期、目标价)。在没有充分技术信息前不报价,改为承诺\"收到工况信息后由技术人员评估并 1-2 个工作日内给出方案\"。",
  },
  {
    id: "minimal",
    label: "极简礼貌",
    text: "用礼貌中文外部口吻回信。一句话确认收到,然后只问 1-2 个最关键的信息(通常是产品/型号 + 数量)。不连续追问其他细节,等客户回复后再判断下一步。",
  },
];

const EMAIL_FIELDS: FormField[] = [
  { name: "address", label: "邮箱地址", required: true,
    placeholder: "sales@yourco.com",
    hint: "agent 收发邮件用的邮箱账号" },
  { name: "password", label: "密码 / 授权码", type: "password", required: true,
    hint: "邮箱密码或应用专用授权码(Gmail / QQ 邮箱等需用授权码)" },
  { name: "imap_host", label: "IMAP 服务器", required: true,
    placeholder: "imap.gmail.com", hint: "收件服务器地址" },
  { name: "imap_port", label: "IMAP 端口", placeholder: "993",
    hint: "可选,默认 993" },
  { name: "smtp_host", label: "SMTP 服务器", required: true,
    placeholder: "smtp.gmail.com", hint: "发件服务器地址" },
  { name: "smtp_port", label: "SMTP 端口", placeholder: "587",
    hint: "可选,默认 587" },
  { name: "allowed_users", label: "允许的发件人", placeholder: "可选,逗号分隔",
    hint: "硬白名单 gate — 只接收这些地址的邮件,其他直接忽略。留空则接收全部。Owner 邮箱会自动放行,不必重复填。" },
  { name: "owner_users", label: "Owner 邮箱", placeholder: "可选,逗号分隔",
    hint: "这些地址发来的邮件视为内部指令(由您本人或同事下达);其他地址按外部客户接待——agent 用礼貌的外部口吻回复并自动登记线索。留空则所有发件人都按客户处理。" },
  { name: "persona", label: "Agent 角色",
    placeholder: "默认:助理",
    hint: "回复给客户时 agent 自称的角色,如\"销售助理小张\"。留空则默认为\"助理\"。" },
  { name: "signature", label: "邮件落款",
    placeholder: "可选,多行",
    hint: "客户接待模式回信的落款。留空则使用 bot 邮箱地址本身。",
    multiline: true },
  { name: "customer_reply_guide", label: "客户接待指南",
    placeholder: "选一个 preset 风格作为起点,可继续编辑;留空则使用默认积极引导话术",
    hint: "客户模式下 agent 遵循的指导原则。可写明口吻、要问的信息、承诺时效、禁忌等。",
    multiline: true,
    presets: CUSTOMER_REPLY_PRESETS },
  { name: "home_address", label: "默认推送地址", placeholder: "可选",
    hint: "可选 — 定时任务 / 主动推送的默认收件地址" },
];

interface ChannelSetupProps {
  profileId: string;
  channel: string;
  onChange?: () => void;
}

/**
 * Dispatches the per-channel setup component by channel name.
 *
 * Feishu + WeChat get dedicated QR-flow components; everything else
 * uses the generic ``FormBotSetup`` with channel-specific field spec.
 */
export function ChannelSetup({ profileId, channel, onChange }: ChannelSetupProps) {
  if (channel === "feishu") {
    return <FeishuSetup profileId={profileId} onChange={onChange} />;
  }
  if (channel === "weixin") {
    return <WeixinSetup profileId={profileId} onChange={onChange} />;
  }
  if (channel === "wecom") {
    return <WecomSetup profileId={profileId} onChange={onChange} />;
  }
  if (channel === "telegram") {
    return (
      <FormBotSetup
        profileId={profileId} channel="telegram"
        channelDisplay="Telegram"
        setupGuideUrl="https://t.me/BotFather"
        fields={TELEGRAM_FIELDS} onChange={onChange}
      />
    );
  }
  if (channel === "discord") {
    return (
      <FormBotSetup
        profileId={profileId} channel="discord"
        channelDisplay="Discord"
        setupGuideUrl="https://discord.com/developers/applications"
        fields={DISCORD_FIELDS} onChange={onChange}
      />
    );
  }
  if (channel === "slack") {
    return (
      <FormBotSetup
        profileId={profileId} channel="slack"
        channelDisplay="Slack"
        setupGuideUrl="https://api.slack.com/apps"
        fields={SLACK_FIELDS} onChange={onChange}
      />
    );
  }
  if (channel === "dingtalk") {
    return <DingtalkSetup profileId={profileId} onChange={onChange} />;
  }
  if (channel === "email") {
    return (
      <FormBotSetup
        profileId={profileId} channel="email"
        channelDisplay="邮件 Email"
        setupGuideUrl="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/email"
        fields={EMAIL_FIELDS} onChange={onChange}
      />
    );
  }
  return (
    <div style={{ padding: 16, color: "var(--muted)" }}>
      Unsupported channel: {channel}
    </div>
  );
}
