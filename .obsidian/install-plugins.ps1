[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$pluginRoot = Join-Path $PSScriptRoot 'plugins'

$plugins = @(
    @{ Id = 'calendar'; Repo = 'liamcain/obsidian-calendar-plugin'; Tag = '1.5.10' },
    @{ Id = 'dataview'; Repo = 'blacksmithgu/obsidian-dataview' },
    @{ Id = 'obsidian-git'; Repo = 'Vinzent03/obsidian-git' },
    @{ Id = 'obsidian-linter'; Repo = 'platers/obsidian-linter' },
    @{ Id = 'omnisearch'; Repo = 'scambier/obsidian-omnisearch' },
    @{ Id = 'obsidian-tasks-plugin'; Repo = 'obsidian-tasks-group/obsidian-tasks' },
    @{ Id = 'templater-obsidian'; Repo = 'SilentVoid13/Templater' }
)

New-Item -ItemType Directory -Force -Path $pluginRoot | Out-Null

foreach ($plugin in $plugins) {
    $destination = Join-Path $pluginRoot $plugin.Id
    New-Item -ItemType Directory -Force -Path $destination | Out-Null

    foreach ($file in @('manifest.json', 'main.js', 'styles.css')) {
        $release = if ($plugin.Tag) { "download/$($plugin.Tag)" } else { 'latest/download' }
        $source = "https://github.com/$($plugin.Repo)/releases/$release/$file"
        $target = Join-Path $destination $file

        try {
            Invoke-WebRequest -Uri $source -OutFile $target -UseBasicParsing
            Write-Host "Installed $($plugin.Id)/$file"
        }
        catch {
            if ($file -eq 'styles.css') {
                Remove-Item -ErrorAction SilentlyContinue -LiteralPath $target
                Write-Host "Skipped optional $($plugin.Id)/$file"
                continue
            }

            throw "Failed to install $($plugin.Id)/$file from $source. $($_.Exception.Message)"
        }
    }
}

Write-Host 'Obsidian community plugins are installed. Restart Obsidian to load them.'
