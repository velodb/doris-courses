/* A / Lab Manual. Renderer only. No authentication, network, course execution or resource APIs. */
const { data: MODEL, copy: COPY } = JSON.parse(
  document.getElementById("preview-input").textContent,
);
const MODULES = MODEL.course.modules;
const VIEW_NAMES = {
  activity: "活动入口",
  "sign-in": "托管登录示意",
  "first-login": "首次改密",
  mfa: "MFA 验证",
  "login-error": "登录失败",
  workspace: "本人首页",
  course: "课程目录",
  lesson: "课程阅读",
  terminal: "终端引导",
  external: "外部工具边界",
  "no-activity": "无活动",
  ended: "活动结束",
  "not-ready": "环境未就绪",
  denied: "无权限",
  expired: "会话到期",
  "signed-out": "退出后",
  help: "账号帮助",
};
const LEVELS = {
  1: "Build the foundation",
  2: "Design and query with intent",
  3: "Operate for performance and resilience",
};
const PREFERENCE_KEY = "doris-workshop-ui-preview";
let preferences = { density: "comfortable", reading: 17, login: "first" };
try {
  preferences = {
    ...preferences,
    ...JSON.parse(localStorage.getItem(PREFERENCE_KEY) || "{}"),
  };
} catch {}
let currentTool = { name: "Notebooks", target: "jupyter / lab" },
  lastModule = 1;
const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const arrow = (external = false) =>
  `<span class="arrow" aria-hidden="true">${external ? "↗" : "→"}</span>`;
function link(route, text, kind = "text-link", extra = "") {
  return `<a class="${kind}" href="#${route}" data-route="${route}" ${extra}>${text}${["primary", "secondary"].includes(kind) ? arrow(route === "external") : ""}</a>`;
}
function native(name, target, kind = "text-link", text = name) {
  return link(
    "external",
    text,
    kind,
    `data-tool="${escapeHtml(name)}" data-target="${escapeHtml(target)}"`,
  );
}
function locationState() {
  const [raw, index] = location.hash.slice(1).split("/");
  const view = Object.hasOwn(VIEW_NAMES, raw) ? raw : "activity";
  const module = Math.max(
    1,
    Math.min(MODULES.length, Number(index) || lastModule),
  );
  return { view, module };
}
function previewBar(view) {
  return `<a class="skip" href="#main" data-main-focus>Skip to content</a><div class="preview-bar"><div><strong>A / 实验手册 · 深化稿</strong><span class="preview-label">　示例活动与身份 · 离线预览</span></div><div class="preview-controls"><label>查看页面 <select id="view-picker" aria-label="查看原型页面">${Object.entries(
    VIEW_NAMES,
  )
    .map(
      ([id, label]) =>
        `<option value="${id}" ${id === view ? "selected" : ""}>${label}</option>`,
    )
    .join(
      "",
    )}</select></label><details class="preview-settings"><summary>预览设置</summary><div class="preview-options"><label>布局密度<select id="density-picker"><option value="comfortable" ${preferences.density === "comfortable" ? "selected" : ""}>舒展</option><option value="compact" ${preferences.density === "compact" ? "selected" : ""}>紧凑</option></select></label><label>阅读字号<select id="reading-picker">${[16, 17, 18, 20].map((x) => `<option value="${x}" ${Number(preferences.reading) === x ? "selected" : ""}>${x}px</option>`).join("")}</select></label><label>登录情形<select id="login-picker"><option value="first" ${preferences.login === "first" ? "selected" : ""}>首次改密</option><option value="returning" ${preferences.login === "returning" ? "selected" : ""}>已有账号</option><option value="mfa" ${preferences.login === "mfa" ? "selected" : ""}>改密后 MFA</option></select></label><p>仅保存布局与字号偏好。登录为只读示意，不接收密码，不保存学习记录。</p></div></details><a href="../review/index.html">初稿对比 ↗</a></div></div>`;
}
function productShell(
  content,
  { publicPage = false, section = "workspace" } = {},
) {
  return `<header class="topbar"><a class="brand" href="#activity" data-route="activity"><img src="${MODEL.assets.logo}" alt="Apache Doris"><span>WORKSHOP</span></a>${publicPage ? "" : `<div class="identity"><span class="mono activity-id">${MODEL.activity.id}</span><span class="mono learner">${MODEL.assignment.learner_id}</span>${link("signed-out", "Sign out of this entry")}</div>`}</header>${
    publicPage
      ? ""
      : `<nav class="topnav" aria-label="Workshop navigation">${[
          ["workspace", "My workspace"],
          ["course", "Course outline"],
          ["terminal", "Web Terminal"],
        ]
          .map(
            ([route, label]) =>
              `<a href="#${route}" data-route="${route}" ${section === route ? 'aria-current="page"' : ""}>${label}</a>`,
          )
          .join("")}</nav>`
  }<main class="main" id="main" tabindex="-1">${content}<footer class="footer"><span>Apache Doris Workshop${publicPage ? "" : ` · ${MODEL.activity.id} / ${MODEL.assignment.learner_id}`}</span><span>${link("help", "Account help")}</span></footer></main>`;
}
function breadcrumb(last, publicPage = false) {
  return `<nav class="breadcrumb" aria-label="Breadcrumb">${link("activity", "Workshops")}${publicPage ? "" : `<span aria-hidden="true">/</span>${link("workspace", MODEL.activity.id)}`}<span aria-hidden="true">/</span><span aria-current="page">${escapeHtml(last)}</span></nav>`;
}
function fileNotice() {
  return `<aside class="notice"><p>Download your files before the workshop ends or your environment is replaced.</p></aside>`;
}

function activity() {
  return productShell(
    `<div class="page-head activity-heading"><h1>Workshops</h1></div><article class="activity-row"><span class="number" aria-hidden="true">01</span><div><h2>${MODEL.course.title}</h2><span class="mono muted">${MODEL.activity.id}</span></div>${link("sign-in", "Sign in to workshop", "primary")}</article><p class="activity-hint">Use the account provided by your organizer.</p>`,
    { publicPage: true },
  );
}

function auth(view) {
  const first = view === "first-login",
    mfa = view === "mfa";
  const destination = first
    ? preferences.login === "mfa"
      ? "mfa"
      : "workspace"
    : mfa
      ? "workspace"
      : preferences.login === "returning"
        ? "workspace"
        : "first-login";
  const title = first
    ? "Change password"
    : mfa
      ? "Verify your sign-in"
      : "Sign in";
  return `<main class="auth" id="main" tabindex="-1"><section class="auth-box" aria-label="Hosted authentication visual preview"><img src="${MODEL.assets.logo}" alt="Apache Doris"><h1>${title}</h1>${first || mfa ? `<p>${first ? "Set a new password." : "Enter your authenticator code."}</p>` : ""}${view === "login-error" ? `<div class="auth-error" role="alert">${COPY["login-error"].error}</div>` : ""}${first ? '<label for="new-password">New password</label><input id="new-password" type="password" readonly placeholder="Preview only" aria-describedby="password-policy"><p class="auth-hint" id="password-policy">14+ characters: uppercase, lowercase, numbers and symbols.</p><label for="confirm-password">Confirm password</label><input id="confirm-password" type="password" readonly placeholder="Preview only">' : mfa ? '<label for="authentication-code">Authentication code</label><input id="authentication-code" readonly inputmode="numeric" placeholder="6-digit code">' : `<label for="username">Username</label><input id="username" value="${MODEL.assignment.learner_id}" readonly autocomplete="off"><label for="password">Password</label><input id="password" type="password" readonly placeholder="Preview only" autocomplete="off">`}<button type="button" class="primary" data-route="${destination}">${first ? "Change password" : mfa ? "Verify" : "Sign in"}</button></section></main>`;
}
function toolRow(number, name, route, target, icon = false) {
  return `<a class="tool-row" href="#${route}" data-route="${route}" ${target ? `data-tool="${escapeHtml(name)}" data-target="${escapeHtml(target)}"` : ""}><span class="tool-number" aria-hidden="true">${number}</span><div><h3>${icon ? `<img class="jupyter" src="${MODEL.assets.jupyter}" alt="Jupyter">` : ""}${name}</h3>${name === "Doris · single FE" ? '<p>Single-node sandbox · 1 FE + 1 BE. Start it in Lab 1.</p>' : ""}</div>${arrow(route === "external")}</a>`;
}

function workspace() {
  return productShell(
    `${breadcrumb("My workspace")}<div class="page-head"><h1>My workspace</h1><span class="badge">Assigned to ${MODEL.assignment.learner_id}</span></div><div class="workspace-grid"><section class="course-board"><div class="eyebrow">01 / Course</div><h2>Real-time Analytics<br>with Apache Doris</h2>${link("course", "Open course", "primary")}<span class="folio" aria-hidden="true">01</span></section><section><div class="tools-head"><h2>Tools</h2><p>Open separately ↗</p></div>${toolRow("02", "Notebooks", "external", "jupyter / lab", true)}${toolRow("03", "Web Terminal", "terminal")}${toolRow("04", "Doris · single FE", "external", "doris /")}<div class="fe-links"><span class="muted">Multi-FE</span>${[
      "fe1",
      "fe2",
      "fe3",
    ]
      .filter((id) => MODEL.assignment.service_ids.includes(id))
      .map((id, index) =>
        native(
          `Doris · FE ${index + 1}`,
          id + " /",
          "text-link",
          `FE ${index + 1} ↗`,
        ),
      )
      .join("")}</div><div class="service-guidance"><p>Lab 9 only · a separate cluster with 3 FEs + 3 BEs. Follow Lab 9 to stop the single-node sandbox and start this cluster before opening FE 1–3.</p></div></section></div>${fileNotice()}`,
  );
}

function course() {
  return productShell(
    `${breadcrumb("Course outline")}<div class="page-head"><div><div class="eyebrow">rta-main / Course outline</div><h1>Real-time Analytics<br>with Apache Doris</h1></div>${native("Notebooks", "jupyter / lab", "secondary", "Open notebooks")}</div>${Object.entries(
      LEVELS,
    )
      .map(
        ([level, title]) =>
          `<section class="level-block" aria-labelledby="level-${level}"><div class="section-heading"><h2 id="level-${level}">${title}</h2><span class="muted">LEVEL ${level}</span></div>${MODULES.map(
            (module, index) => ({ ...module, index }),
          )
            .filter((module) => module.level === Number(level))
            .map(
              (module) =>
                `<article class="module"><span class="module-number" aria-hidden="true">${String(module.index + 1).padStart(2, "0")}</span><div><h3>${module.title}</h3></div><div class="actions">${link(`lesson/${module.index + 1}`, "Read lesson →", "text-link", `aria-label="Read lesson ${module.index + 1}: ${escapeHtml(module.title)}"`)}${native(`Lab ${module.index + 1}`, module.lab_path, "text-link", "Open Lab ↗")}</div></article>`,
            )
            .join("")}</section>`,
      )
      .join("")}`,
    { section: "course" },
  );
}
function lesson(index) {
  const module = MODULES[index - 1];
  lastModule = index;
  return productShell(
    `${breadcrumb("Module " + index)}<header class="lesson-heading"><div class="eyebrow">Level ${module.level} · Module ${String(index).padStart(2, "0")}</div><h1>${module.title}</h1></header><div class="reading-layout"><article class="article" aria-label="Lesson content">${module.html}<nav class="lesson-end" aria-label="Lesson navigation">${link("course", "Course outline", "secondary")}${index < MODULES.length ? link(`lesson/${index + 1}`, "Next lesson", "primary") : link("workspace", "My workspace", "primary")}</nav></article><aside class="reading-side" aria-label="Lesson tools">${native(`Lab ${index}`, module.lab_path, "primary", `Open Lab ${index}`)}<p>Opens in a new tab.</p><details class="toc" ${window.innerWidth > 700 ? "open" : ""}><summary>In this lesson</summary><nav aria-label="On this page">${module.toc.map((item) => `<a href="#${item.id}" data-section="${item.id}">${escapeHtml(item.title)}</a>`).join("")}</nav></details>${link("course", "Back to course")}</aside></div>`,
    { section: "course" },
  );
}
function terminal() {
  return productShell(
    `${breadcrumb("Web Terminal")}<section class="guide"><h1>Web Terminal</h1><ol class="steps"><li><strong>Open JupyterLab</strong></li><li><strong>File → New → Terminal</strong></li></ol><details class="terminal-help"><summary>Existing terminals</summary><p>Find them under Running Terminals and Kernels in JupyterLab.</p></details><div class="actions">${native("JupyterLab", "jupyter / lab", "primary", "Open JupyterLab")}${link("workspace", "My workspace", "secondary")}</div></section>`,
    { section: "terminal" },
  );
}

function external() {
  const sandboxHint = currentTool.name.startsWith("Doris")
    ? "<p>Start the sandbox in your Lab first.</p>"
    : "";
  return productShell(
    `${breadcrumb("Your tools")}<section class="state"><div class="boundary-label">离线预览 · 未连接服务</div><h1>${escapeHtml(currentTool.name)}</h1>${sandboxHint}<span class="boundary-path mono">${escapeHtml(currentTool.target)}</span><div class="actions">${link(`lesson/${lastModule}`, "Back to lesson", "primary")}${link("workspace", "My workspace", "secondary")}</div></section>`,
  );
}

function status(view) {
  const copy = COPY[view];
  const assigned = view === "not-ready";
  return productShell(
    `${breadcrumb("Workshop access", !assigned)}<section class="state"><div class="eyebrow">${copy.label}</div><h1>${copy.title}</h1><p>${copy.body}</p>${copy.detail || assigned ? `<div class="state-detail">${copy.detail ? `<p>${copy.detail}</p>` : ""}${assigned ? `<p>Activity <strong class="mono">${MODEL.activity.id}</strong> · Learner <strong class="mono">${MODEL.assignment.learner_id}</strong></p>` : ""}</div>` : ""}<div class="actions">${link(copy.destination, copy.primary, "primary")}${view === "help" ? "" : link("help", "Account help", "secondary")}</div></section>`,
    { publicPage: !assigned },
  );
}
function setPreferences() {
  document.body.dataset.density = ["comfortable", "compact"].includes(
    preferences.density,
  )
    ? preferences.density
    : "comfortable";
  document.documentElement.style.setProperty(
    "--reading",
    (Number(preferences.reading) || 17) + "px",
  );
}
function savePreferences() {
  // Do not persist identity, routes, auth state, module state or quiz results.
  try {
    localStorage.setItem(
      PREFERENCE_KEY,
      JSON.stringify({
        density: preferences.density,
        reading: preferences.reading,
      }),
    );
  } catch {}
}
function go(route) {
  if (location.hash === "#" + route) render(true);
  else location.hash = route;
}
function render(focus = false) {
  const { view, module } = locationState();
  setPreferences();
  document.title =
    (view === "lesson" ? MODULES[module - 1].title : VIEW_NAMES[view]) +
    " · Apache Doris Workshop";
  let content;
  if (["sign-in", "first-login", "mfa", "login-error"].includes(view))
    content = auth(view);
  else
    content =
      view === "activity"
        ? activity()
        : view === "workspace"
          ? workspace()
          : view === "course"
            ? course()
            : view === "lesson"
              ? lesson(module)
              : view === "terminal"
                ? terminal()
                : view === "external"
                  ? external()
                  : status(view);
  document.getElementById("app").innerHTML = previewBar(view) + content;
  document.querySelectorAll(".article table").forEach((table) => {
    const wrap = document.createElement("div");
    wrap.className = "scrollbox";
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", "Scrollable lesson table");
    table.replaceWith(wrap);
    wrap.append(table);
  });
  document.querySelectorAll(".article pre").forEach((pre) => {
    pre.tabIndex = 0;
    pre.setAttribute("role", "region");
    pre.setAttribute("aria-label", "Scrollable code example");
  });
  document
    .getElementById("view-picker")
    .addEventListener("change", (event) =>
      go(
        event.target.value === "lesson"
          ? `lesson/${lastModule}`
          : event.target.value,
      ),
    );
  for (const [id, key] of [
    ["density-picker", "density"],
    ["reading-picker", "reading"],
    ["login-picker", "login"],
  ])
    document.getElementById(id).addEventListener("change", (event) => {
      preferences[key] = event.target.value;
      setPreferences();
      if (key !== "login") savePreferences();
    });
  const pageTitle = document
    .querySelector("#main h1")
    .textContent.replace(/\s+/g, " ");
  document.title = pageTitle + " · Apache Doris Workshop";
  document.getElementById("announcement").textContent = pageTitle;
  if (focus) {
    const heading = document.querySelector("#main h1");
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
}
document.addEventListener("click", (event) => {
  const target = event.target.closest(
    "[data-route],[data-section],[data-main-focus]",
  );
  if (!target) return;
  event.preventDefault();
  if (target.hasAttribute("data-main-focus")) {
    document.getElementById("main").focus();
    return;
  }
  if (target.dataset.section) {
    const heading = document.getElementById(target.dataset.section);
    if (heading) {
      heading.focus({ preventScroll: true });
      window.scrollTo({
        top: heading.getBoundingClientRect().top + window.scrollY - 24,
        behavior: "instant",
      });
    }
    return;
  }
  if (target.dataset.tool)
    currentTool = {
      name: target.dataset.tool,
      target: target.dataset.target || "",
    };
  go(target.dataset.route);
});
window.addEventListener("hashchange", () => render(true));
render();
