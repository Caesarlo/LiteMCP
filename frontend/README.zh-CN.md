# LiteMCP 前端

[English](README.md) | 简体中文

[LiteMCP](../README.zh-CN.md) 的管理控制台前端——React 19、TypeScript、Vite、HeroUI 与 Tailwind CSS。产品整体介绍见根目录 [README](../README.zh-CN.md) 与[前端架构文档](../docs/architecture/06-frontend.md)；本文只覆盖在 `frontend/` 目录下的开发工作。

> [!IMPORTANT]
> 当前仍是原始的 HeroUI/Vite 初始模板，并非 LiteMCP 产品界面，尚未接入任何服务管理 API，只能用于验证前端工程链路是否正常运行。已验证的权威状态请查看 [`../feature_list.json`](../feature_list.json) 和 [`../progress.md`](../progress.md)。

## 环境要求

- 当前受支持的 Node.js LTS 版本
- npm

## 快速开始

```bash
cd frontend
npm install
npm run dev
```

开发服务器默认运行在 `http://localhost:5173`。

## 可用脚本

| 命令 | 说明 |
| --- | --- |
| `npm run dev` | 启动 Vite 开发服务器 |
| `npm run build` | 类型检查（`tsc`）并构建生产包（`vite build`） |
| `npm run test` | 运行测试套件（Vitest） |
| `npm run lint` | 代码检查并自动修复（ESLint） |
| `npm run preview` | 本地预览生产构建结果 |

## 目录结构

```
frontend/
├── src/
│   ├── components/   # 共享 UI 组件
│   ├── layouts/       # 页面布局壳层
│   ├── pages/          # 路由级页面
│   ├── config/         # 前端运行时配置
│   ├── styles/          # Tailwind / 全局样式
│   ├── types/            # 共享 TypeScript 类型
│   └── test/               # 测试初始化与工具
├── public/
├── index.html
└── vite.config.ts
```

## 测试

测试基于 [Vitest](https://vitest.dev/)，配合 [@testing-library/react](https://testing-library.com/docs/react-testing-library/intro/) 和 [MSW](https://mswjs.io/) 做接口模拟：

```bash
npm run test          # watch 模式
npm run test -- --run # 单次运行（CI / make ci-fast 使用）
```

## 参与贡献

提交前请运行 `npm run lint` 和 `npm run build`。本项目遵循 [`../AGENTS.md`](../AGENTS.md) 中描述的仓库级 TDD 与特性验证工作流，开始非平凡的前端开发前请先阅读该文档。
