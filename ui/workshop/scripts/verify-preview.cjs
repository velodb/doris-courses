/* UI-only validation. Does not start or call workshop control services. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs"),
  path = require("path"),
  { pathToFileURL } = require("url");
const root = path.resolve(__dirname, "..");
const executable = process.env.PLAYWRIGHT_EXECUTABLE;
const views = [
  "activity",
  "sign-in",
  "first-login",
  "mfa",
  "login-error",
  "workspace",
  "course",
  "lesson",
  "terminal",
  "external",
  "no-activity",
  "ended",
  "not-ready",
  "denied",
  "expired",
  "signed-out",
  "help",
];
const sizes = [
  { width: 1440, height: 1000 },
  { width: 768, height: 1024 },
  { width: 390, height: 844 },
  { width: 320, height: 780 },
];
let browser;
(async () => {
  browser = await chromium.launch({
    headless: true,
    executablePath: executable,
  });
  const page = await browser.newPage();
  const errors = [],
    requests = [],
    issues = [],
    screenshots = [],
    contrast = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (!r.url().startsWith("file:") && !r.url().startsWith("data:"))
      requests.push(r.url());
  });
  await page.goto(pathToFileURL(path.join(root, "dist/prototype.html")).href);
  await page.evaluate(() => document.fonts.ready);
  async function choose(view) {
    await page.selectOption("#view-picker", view);
    await page.waitForFunction(
      (v) =>
        document.getElementById("view-picker").value === v &&
        location.hash.slice(1).split("/")[0] === v,
      view,
    );
  }
  async function assertLayout(label) {
    await page.evaluate(async () => {
      const imgs = [...document.images];
      imgs.forEach((i) => (i.loading = "eager"));
      await Promise.all(imgs.map((i) => i.decode().catch(() => {})));
    });
    const data = await page.evaluate(() => ({
      width: innerWidth,
      scroll: document.documentElement.scrollWidth,
      headings: document.querySelectorAll("h1").length,
      missing: [...document.images]
        .filter((i) => !i.complete || !i.naturalWidth)
        .map((i) => i.alt),
      readonly: [...document.querySelectorAll("input")].every(
        (i) => i.readOnly,
      ),
    }));
    if (
      data.scroll > data.width + 1 ||
      data.headings !== 1 ||
      data.missing.length ||
      !data.readonly
    )
      issues.push({ label, ...data });
  }
  for (const size of sizes) {
    await page.setViewportSize(size);
    for (const view of views) {
      await choose(view);
      await assertLayout(`${view}/${size.width}`);
      if (size.width === 1440) {
        const failures = await page.evaluate(() => {
          const rgb = (s) => s.match(/[\d.]+/g).map(Number),
            l = (c) =>
              c
                .slice(0, 3)
                .map((v) => {
                  v /= 255;
                  return v <= 0.04045
                    ? v / 12.92
                    : ((v + 0.055) / 1.055) ** 2.4;
                })
                .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0),
            issues = [];
          for (const el of document.querySelectorAll("#main *")) {
            if (
              el.closest('[aria-hidden="true"]') ||
              !el.getClientRects().length ||
              ![...el.childNodes].some(
                (n) => n.nodeType === 3 && n.textContent.trim(),
              )
            )
              continue;
            const cs = getComputedStyle(el);
            let p = el,
              bg = [255, 255, 255, 1];
            while (p) {
              const c = rgb(getComputedStyle(p).backgroundColor);
              if (c.length === 3 || c[3] === 1) {
                bg = c;
                break;
              }
              p = p.parentElement;
            }
            const a = l(rgb(cs.color)),
              b = l(bg),
              ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
              large =
                parseFloat(cs.fontSize) >= 24 ||
                (parseFloat(cs.fontSize) >= 18.66 &&
                  Number(cs.fontWeight) >= 700);
            if (ratio < (large ? 3 : 4.5) - 0.01)
              issues.push({ text: el.textContent.trim().slice(0, 55), ratio });
          }
          return issues;
        });
        if (failures.length) contrast.push({ view, failures });
      }
      if (
        [
          "activity",
          "workspace",
          "course",
          "lesson",
          "sign-in",
          "denied",
          "signed-out",
          "not-ready",
        ].includes(view)
      ) {
        const filename = `${view}-${size.width}.png`;
        await page.screenshot({
          path: path.join(root, "review/selected", filename),
          fullPage: ["denied", "signed-out", "not-ready"].includes(view),
        });
        screenshots.push(filename);
      }
    }
  }
  // Verify every real lesson at desktop and narrow widths, including its first table and code block.
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    for (let i = 1; i <= 10; i++) {
      await page.evaluate((n) => (location.hash = `lesson/${n}`), i);
      await page.waitForFunction(
        (n) =>
          location.hash === `#lesson/${n}` &&
          document
            .querySelector(".lesson-heading .eyebrow")
            ?.textContent.includes(String(n).padStart(2, "0")),
        i,
      );
      await assertLayout(`lesson-${i}/${width}`);
      if (
        await page
          .locator(".article")
          .innerText()
          .then((x) => x.includes("layout placeholder"))
      )
        issues.push({ label: `lesson-${i}`, reason: "placeholder" });
      if (await page.locator(".article pre").count()) {
        const scrollable = await page
          .locator(".article pre")
          .first()
          .evaluate(
            (el) =>
              el.tabIndex === 0 && getComputedStyle(el).overflowX === "auto",
          );
        if (!scrollable)
          issues.push({
            label: `lesson-${i}`,
            reason: "code not keyboard-scrollable",
          });
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await choose("activity");
  await page
    .getByRole("link", { name: "Sign in to workshop", exact: true })
    .click();
  await page.waitForSelector("#username");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForSelector("#new-password");
  await page
    .getByRole("button", { name: "Change password", exact: true })
    .click();
  await page.waitForSelector(".course-board");
  await page.getByRole("link", { name: "Open course", exact: true }).click();
  await page.waitForSelector(".module");
  if ((await page.locator(".module").count()) !== 10)
    issues.push({ reason: "Module count" });
  await page.getByRole("link", { name: /^Read lesson 1:/ }).click();
  await page.waitForSelector(".article");
  await page
    .getByRole("link", { name: "Open Lab 1", exact: true })
    .first()
    .click();
  await page.waitForSelector(".boundary-label");
  if ((await page.locator("h1").innerText()) !== "Lab 1")
    issues.push({ reason: "Lab target" });
  await page.getByRole("link", { name: "Back to lesson", exact: true }).click();
  await page.waitForSelector(".article");
  await page.goBack();
  await page.waitForSelector(".boundary-label");
  await page.goForward();
  await page.waitForSelector(".article");
  await page.locator('[data-section="module-goal"]').first().click();
  if ((await page.evaluate(() => document.activeElement.id)) !== "module-goal")
    issues.push({ reason: "TOC focus" });
  await choose("workspace");
  await page.locator("main").getByRole("link", { name: "Web Terminal", exact: true }).click();
  await page.waitForSelector(".steps");
  await page
    .getByRole("link", { name: "Open JupyterLab", exact: true })
    .click();
  await page.waitForSelector(".boundary-label");
  await choose("workspace");
  await page
    .getByRole("link", { name: "Sign out of this entry", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector("h1")?.textContent === "Signed out of this entry",
  );
  await page
    .getByRole("link", { name: "Back to activities", exact: true })
    .click();
  await page.waitForSelector(".activity-row");
  // Existing-account and MFA transitions are preview controls, not invented authentication endpoints.
  await page.locator(".preview-settings").evaluate((e) => (e.open = true));
  await page.selectOption("#login-picker", "returning");
  await choose("sign-in");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForSelector(".course-board");
  await page.locator(".preview-settings").evaluate((e) => (e.open = true));
  await page.selectOption("#login-picker", "mfa");
  await choose("sign-in");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForSelector("#new-password");
  await page
    .getByRole("button", { name: "Change password", exact: true })
    .click();
  await page.waitForSelector("#authentication-code");
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await page.waitForSelector(".course-board");
  await page.locator(".preview-settings").evaluate((e) => (e.open = true));
  await page.selectOption("#density-picker", "compact");
  await page.selectOption("#reading-picker", "20");
  await page.reload();
  await page.waitForSelector(".course-board");
  const preferences = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("doris-workshop-ui-preview")),
  );
  if (
    Object.keys(preferences).sort().join(",") !== "density,reading" ||
    preferences.density !== "compact" ||
    Number(preferences.reading) !== 20
  )
    issues.push({ reason: "Preference-only persistence", preferences });
  await page.setViewportSize({ width: 320, height: 780 });
  await choose("lesson");
  await assertLayout("20px-reading-320");
  // Keep the delivered default screenshots and opened prototype in comfortable mode.
  await page.evaluate(() =>
    localStorage.removeItem("doris-workshop-ui-preview"),
  );
  await page.reload();
  await page.waitForSelector(".article");
  await choose("workspace");
  await page.keyboard.press("Tab");
  const keyboard = await page.evaluate(() =>
    document.activeElement.matches("a,button,select,input,summary"),
  );
  if (!keyboard) issues.push({ reason: "Keyboard focus" });
  // The portable component reference shares the same style source.
  await page.goto(pathToFileURL(path.join(root, "dist/components.html")).href);
  await page.evaluate(() => document.fonts.ready);
  await assertLayout("components/320");
  await page.screenshot({
    path: path.join(root, "review/selected/components-320.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: path.join(root, "review/selected/components-1440.png"),
    fullPage: true,
  });
  const report = {
    ok:
      !errors.length && !requests.length && !issues.length && !contrast.length,
    view_checks: views.length * sizes.length,
    real_lesson_checks: 20,
    sizes,
    errors,
    external_requests: requests,
    issues,
    contrast_failures: contrast,
    keyboard,
    screenshots,
    flows: [
      "first login",
      "existing account",
      "MFA",
      "course to real lesson to Lab boundary and back",
      "history back/forward",
      "TOC focus",
      "terminal",
      "sign-out",
      "only UI preference persistence",
    ],
    checked_at: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(root, "review/selected/verification.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(
    JSON.stringify({ ...report, screenshots: screenshots.length }, null, 2),
  );
  await browser.close();
  if (!report.ok) process.exitCode = 1;
})().catch(async (e) => {
  console.error(e);
  await browser?.close();
  process.exitCode = 1;
});
