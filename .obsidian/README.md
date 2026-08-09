# Obsidian configuration

Open the repository root as an Obsidian vault.

The checked-in configuration enables the core features used for project documentation and these community plugins:

- Calendar
- Dataview
- Git
- Linter
- Omnisearch
- Tasks
- Templater

To install or update all community plugins, run:

```powershell
pwsh -File .obsidian/install-plugins.ps1
```

Obsidian may ask you to turn off Restricted Mode before loading community plugins. Restart Obsidian after installation.

Personal workspace state (`workspace*.json`) and downloaded plugin bundles are intentionally ignored by Git.
