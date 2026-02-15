# Hook: Audio notification when Claude Code needs permission approval
# Calls AllTalk TTS to speak "Permission needed"

$ErrorActionPreference = "SilentlyContinue"
$alltalkUrl = "http://127.0.0.1:7851"
$tempWav = Join-Path $env:TEMP "claude_hook_permission.wav"

try {
    $body = @{
        input = "Permission needed"
        voice = "Freya.wav"
        model = "tts-1"
        response_format = "wav"
    } | ConvertTo-Json

    Invoke-RestMethod -Uri "$alltalkUrl/v1/audio/speech" `
        -Method Post `
        -ContentType "application/json" `
        -Body $body `
        -OutFile $tempWav `
        -TimeoutSec 8

    if (Test-Path $tempWav) {
        $player = New-Object System.Media.SoundPlayer $tempWav
        $player.PlaySync()
        Remove-Item $tempWav -Force
    }
} catch {
    # Never block Claude
}

exit 0
