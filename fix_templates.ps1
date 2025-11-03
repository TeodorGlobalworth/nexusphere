$files = @(
    "app\templates\base.html",
    "app\templates\admin\dashboard.html",
    "app\templates\admin\experimental.html",
    "app\templates\admin\new_project.html",
    "app\templates\admin\new_user.html",
    "app\templates\admin\projects.html",
    "app\templates\admin\users.html",
    "app\templates\admin\_user_edit_modal.html",
    "app\templates\admin\_user_projects_modal.html",
    "app\templates\auth\login.html",
    "app\templates\errors\403.html",
    "app\templates\errors\404.html",
    "app\templates\errors\413.html",
    "app\templates\errors\500.html",
    "app\templates\project\config.html",
    "app\templates\project\dashboard.html",
    "app\templates\project\history.html",
    "app\templates\project\knowledge_base.html"
)

$totalFixed = 0
foreach ($file in $files) {
    if (Test-Path $file) {
        $content = Get-Content $file -Raw -Encoding UTF8
        $fixed = $content -replace '\{\{\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż\s]+)\s+\}\}', '{{ _(''$1'') }}'
        $matches = ([regex]::Matches($content, '\{\{\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż\s]+\s+\}\}')).Count
        if ($matches -gt 0) {
            Set-Content -Path $file -Value $fixed -Encoding UTF8 -NoNewline
            Write-Host "Fixed $matches instances in $file"
            $totalFixed += $matches
        }
    }
}
Write-Host "`nTotal fixed: $totalFixed instances across $($files.Count) files"
