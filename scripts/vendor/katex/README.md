# KaTeX vendor（静态站点公式渲染）

来源版本：**KaTeX 0.18.4**，从 npm registry 下载：

```text
https://registry.npmjs.org/katex/-/katex-0.18.4.tgz
```

目录内容：

- `katex.min.js`：KaTeX 核心渲染器
- `auto-render.min.js`：扫描 `$..$` / `$$..$$` 并调用 KaTeX 渲染
- `katex.min.css`：KaTeX 样式（`export-site` 会把 `fonts/*.woff2` 转成 base64 内嵌）
- `fonts/*.woff2`：KaTeX 字体
- `LICENSE`：KaTeX MIT 许可证（再分发必须随附）

为什么 vendor：`export-site` 要求**单文件、离线、自包含、无 CDN、不 fetch**，因此不能运行时
从 CDN 拉取 KaTeX。这些文件必须进入 Git（`scripts/vendor/` 当前未被 ignore），否则新 clone
无法离线构建 `study-kb.html`。
