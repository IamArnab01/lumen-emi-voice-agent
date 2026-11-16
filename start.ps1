# One entry point from Windows. Everything runs inside WSL Ubuntu, because Windows
# Smart App Control blocks livekit's native libraries on this machine.
#   .\start.ps1 setup | check | keys | shell
#   .\start.ps1 smoke [ultravox]          text-mode smoke test
#   .\start.ps1 console [B001..B008]      talk to the agent with your laptop mic
#   .\start.ps1 agent   [B001..B008]      run on LiveKit Cloud, join from the Agents Playground
#   .\start.ps1 dev     [B001..B008]      same, auto-reload on code changes
#   .\start.ps1 voicebot [ultravox|cascade] [happy|hindi]   voice bot calls the agent: latency + barge-in
param([string]$Command = "help", [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @())

# No $ErrorActionPreference = "Stop": WSL writes harmless warnings to stderr
# (e.g. "Failed to start the systemd user session"), which would abort the script.
wsl -d Ubuntu --cd "$PSScriptRoot" -- bash scripts/run.sh $Command @Rest
exit $LASTEXITCODE
