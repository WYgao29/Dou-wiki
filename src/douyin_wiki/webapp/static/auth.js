(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  let timer = null;
  let sessionId = null;
  let focusedChannel = false;

  function stop() {
    clearTimeout(timer);
    timer = null;
  }

  function renderSkeleton() {
    [
      ["auth-video-card", "视频下载授权"],
      ["auth-douyin-card", "抖音账号授权"],
    ].forEach(([id, title]) => {
      const card = D.node("article", null, "operation-section");
      card.append(D.node("h2", title), D.node("p", "正在读取授权状态…", "hint-copy"));
      const pulse = D.node("div", null, "skeleton-row");
      card.append(pulse);
      D.$(id).replaceChildren(card);
    });
  }

  function renderChannel(container, channel) {
    container.replaceChildren();
    const card = D.node("article", null, "operation-section");
    card.append(
      D.node("h2", channel.channel_label),
      D.node("p", channel.purpose),
      D.node("p", channel.user_state_label, "status-pill"),
    );
    card.lastChild.dataset.state = channel.user_state;
    card.lastChild.prepend(D.icon(["authorized", "available"].includes(channel.user_state) ? "check" : "alert-circle"));
    card.append(D.node("p", `上次检查：${channel.checked_display || "尚未检查"}`));
    const conclusion = String(channel.conclusion || "").trim();
    if (conclusion && conclusion !== channel.user_state_label) {
      card.append(D.node("p", conclusion));
    }
    if (channel.affected_job_count) {
      card.append(D.node("p", `受影响任务 ${channel.affected_job_count} 个`));
    }
    const technical = [channel.message, channel.detail, channel.cookie_source_label, channel.account_hint]
      .map((line) => String(line || "").trim())
      .filter(Boolean);
    const actions = D.node("div", null, "operation-actions");
    const check = D.node("button", "检查", "secondary-button");
    check.type = "button";
    check.addEventListener("click", async () => {
      check.disabled = true;
      check.textContent = "正在检查…";
      try {
        const data = await load(true);
        const current = data.channels?.[channel.channel];
        D.toast(current?.user_state_label || "检查完成");
      } catch (error) {
        D.toast(error.message || "检查失败");
      } finally {
        check.disabled = false;
        if (check.isConnected) check.textContent = "检查";
      }
    });
    const authorize = D.node("button", "重新授权", "secondary-button");
    authorize.type = "button";
    authorize.addEventListener("click", () => start(channel.channel));
    actions.append(check, authorize);
    if (channel.affected_job_count) {
      const link = D.node("a", "查看受影响任务", "ghost-button");
      link.href = "/jobs?requires_user_action=1";
      link.addEventListener("click", (event) => {
        event.preventDefault();
        D.navigate("/jobs?requires_user_action=1");
      });
      actions.append(link);
    }
    if (technical.length) {
      const copy = D.node("button", "复制诊断信息", "text-button");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        const text = technical.join("\n");
        try {
          await navigator.clipboard.writeText(text);
          D.toast("已复制诊断信息");
        } catch (_error) {
          D.toast("复制失败");
        }
      });
      actions.append(copy);
    }
    card.append(actions);
    container.append(card);
  }

  async function load(refresh = false) {
    const data = await D.api(`/api/auth/status${refresh ? "?refresh=true" : ""}`);
    renderChannel(D.$("auth-video-card"), data.channels.video);
    renderChannel(D.$("auth-douyin-card"), data.channels.douyin);
    const focusDouyin = new URLSearchParams(location.search).get("channel") === "douyin";
    D.$("auth-douyin-card").classList.toggle("auth-channel-focus", focusDouyin);
    if (focusDouyin && !focusedChannel && !D.$("auth-view").classList.contains("hidden")) {
      focusedChannel = true;
      D.$("auth-douyin-card").scrollIntoView({block: "center"});
    }
    const session = D.$("auth-session");
    if (sessionId) {
      const current = await D.api(`/api/auth/sessions/${encodeURIComponent(sessionId)}`);
      session.hidden = false;
      session.replaceChildren(
        D.node("h2", "当前授权会话"),
        D.node("p", current.stage === "waiting_login" ? "请在本机打开的浏览器中完成登录、扫码或验证码。" : current.stage),
        D.node("p", current.error_summary || ""),
      );
      if (current.stage === "succeeded") {
        D.toast(current.retried_job_ids?.length ? `授权成功，已继续 ${current.retried_job_ids.length} 个任务` : "授权已恢复");
        sessionId = null;
        stop();
      } else if (["failed", "timeout", "cancelled", "profile_locked", "account_changed"].includes(current.stage)) {
        D.toast(current.error_summary || "授权未完成");
        sessionId = null;
        stop();
      } else {
        const later = D.node("button", "稍后处理", "secondary-button");
        later.type = "button";
        later.addEventListener("click", async () => {
          await D.api(`/api/auth/sessions/${encodeURIComponent(current.id)}/cancel`, {method: "POST", body: "{}"});
          sessionId = null;
          stop();
          load(true);
        });
        session.append(D.node("div", null, "progress-track is-indeterminate"), later);
      }
    } else {
      session.hidden = true;
    }
    return data;
  }

  async function start(channel) {
    const session = await D.api("/api/auth/sessions", {
      method: "POST",
      body: JSON.stringify({channel}),
    });
    sessionId = session.id;
    D.toast("正在打开本机浏览器，请完成登录。抖库不会读取密码或 Cookie。");
    await load(true);
    stop();
    const tick = async () => {
      try { await load(true); } catch (error) { D.toast(error.message); }
      if (sessionId) timer = setTimeout(tick, 2500);
    };
    timer = setTimeout(tick, 2500);
  }

  window.addEventListener("douku:route", async (event) => {
    if (String(event.detail.path || "").split("?")[0] !== "/settings/auth") {
      stop();
      return;
    }
    D.hideAllViews();
    D.setPage("auth");
    D.setNav("settings-nav");
    D.$("auth-view").classList.remove("hidden");
    document.title = "授权状态 · 抖库";
    focusedChannel = false;
    renderSkeleton();
    load(false).then((data) => {
      const video = data.channels?.video;
      const needsVerify = Boolean(data.pending || data.cached)
        || video?.user_state === "checking"
        || video?.user_state === "unconfirmed"
        || video?.user_state === "unverified";
      if (needsVerify) load(true).catch((error) => D.toast(error.message));
    }).catch((error) => D.toast(error.message));
  });
})();
