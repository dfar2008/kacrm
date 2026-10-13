/**
 * 「CRM 界面导览」标签页内容——真实操作录屏视频。
 * 视频/字幕/poster 由 scripts/record-crm-tour.ts 产出到 public/tour/。
 * 作为向导页第三个标签,切走时整块卸载(video 元素随之停止),切回从 poster 起。
 */
import { useT, useLocale } from "@/lib/i18n";
import { usePermissions } from "@/lib/permissions";

const ASSET = (p: string) => `${import.meta.env.BASE_URL}tour/${p}`;

export function CrmTourVideo({ onEnterCrm }: { onEnterCrm: () => void }) {
  const t = useT();
  const { locale } = useLocale();
  const { can } = usePermissions();

  return (
    <section style={{
      border: "1px solid var(--border)", borderRadius: 10,
      background: "var(--surface)", padding: 16,
    }}>
      <div style={{ marginBottom: 12 }}>
        <span style={{ fontWeight: 600, fontSize: 16 }}>{t("setup.tour_title")}</span>
        <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 4 }}>
          {t("setup.tour_desc")}
        </div>
      </div>
      {/* 16:9 录屏限高到视口的一部分,矮窗口也能一屏看全(含控制条);
          按宽/按高取小者等比缩放、居中。无音轨,字幕轨默认开当前语言。 */}
      <video controls playsInline preload="metadata"
        poster={ASSET("poster.jpg")}
        crossOrigin="anonymous"
        style={{
          maxWidth: "100%", maxHeight: "64vh",
          borderRadius: 8, display: "block", margin: "0 auto",
          border: "1px solid var(--border)", background: "#000", // theme-guard:ignore
        }}>
        <source src={ASSET("crm-tour.mp4")} type="video/mp4" />
        <track kind="subtitles" srcLang="zh" label="中文"
          src={ASSET("crm-tour.zh.vtt")} default={locale === "zh-CN"} />
        <track kind="subtitles" srcLang="en" label="English"
          src={ASSET("crm-tour.en.vtt")} default={locale !== "zh-CN"} />
      </video>
      {can("accounts", "index") && (
        <div style={{ textAlign: "center", paddingTop: 12 }}>
          <button className="btn btn-primary" onClick={onEnterCrm}>
            {t("setup.tour_cta")} →
          </button>
        </div>
      )}
    </section>
  );
}
