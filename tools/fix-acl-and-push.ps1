# fix-acl-and-push.ps1
$ErrorActionPreference = "Stop"

$repo = "https://asmldev.azure.com/asml/AI-Services/_git/AI000-aiproducts-mcp-template"
$remote = "origin"
$branch = "main"

if (-not (Test-Path .git)) { git init }

# Remove explicit Deny ACEs on .git and .git\config (requires admin if policy blocks)
$denySids = @(
  "S-1-5-21-3704998283-76326659-3102379810-3729709296"
)

foreach ($path in @(".git", ".git\\config")) {
  if (Test-Path $path) {
    foreach ($sid in $denySids) {
      & icacls $path /remove:d $sid | Out-Null
    }
    & icacls $path /grant "ASML-COM\\dkuzin:(OI)(CI)F" /T | Out-Null
  }
}

# ensure remote
git remote remove $remote 2>$null
git remote add $remote $repo

# if no commits, create initial
if (-not (git rev-parse --verify HEAD 2>$null)) {
  git add -A
  git commit -m "Initial commit"
}

# ensure branch
git branch -M $branch

# push
git push -u $remote $branch
