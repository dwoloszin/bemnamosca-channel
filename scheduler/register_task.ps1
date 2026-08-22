# ==========================================================================
#  Registra a tarefa diaria do Bem na Mosca no Agendador do Windows.
#  Rode UMA vez, no PowerShell, dentro da pasta do projeto:
#
#      powershell -ExecutionPolicy Bypass -File scheduler\register_task.ps1
#
#  Remover depois:
#      Unregister-ScheduledTask -TaskName "BemNaMoscaDaily" -Confirm:$false
#
#  Ver quando roda / rodou:
#      Get-ScheduledTask BemNaMoscaDaily | Get-ScheduledTaskInfo
#
#  Rodar agora, para testar:
#      Start-ScheduledTask -TaskName "BemNaMoscaDaily"
# ==========================================================================

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Bat        = Join-Path $PSScriptRoot "run_daily.bat"

# Nome PROPRIO deste canal. O template vinha com "AutoYouTubeChannel", que e
# generico: o projeto irmao (GTA News) usaria o mesmo nome e, como o registro
# e feito com -Force, um sobrescreveria o agendamento do outro em silencio.
$TaskName = "BemNaMoscaDaily"

# 10:00 local. Voltou de 11:00 para 10:00 em 17/08 para caber a escada de
# retentativas do carrossel (config daily.*): 4 tentativas de 30 em 30 min
# comecam as 10:00 e terminam por volta das 11:45, e as duas primeiras ainda
# alcancam o slot das 12:00 (o YouTube exige 20 min de margem para agendar).
# Se a tentativa que der certo for tarde demais para as 12:00, o agendamento
# cai para a proxima hora cheia — 13:00, 14:00 (youtube.schedule.hourly_fallback).
#
# ATENCAO: 10:00 ja foi tentado antes e foi abandonado porque o PC costumava
# estar desligado nesse horario, e o StartWhenAvailable so recupera o que o
# Windows marca como perdido. Se voltar a acontecer, o sintoma e LastRunTime
# parado no dia anterior. Nesse caso volte para 11:00 e reduza
# daily.carousel_attempts para 2.
$RunAt = "10:00"

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$Bat`"" -WorkingDirectory $ProjectDir

$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)   # 2h matava a escada de retentativas

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Bem na Mosca: publica promo do loop/oneuse e gera o carrossel de noticias do dia."

Write-Host ""
Write-Host "Tarefa '$TaskName' registrada para rodar todo dia as $RunAt." -ForegroundColor Green
Write-Host "Executa: $Bat"
Write-Host ""
Write-Host "  StartWhenAvailable : se o PC estiver desligado as $RunAt, roda ao ligar"
Write-Host "  IgnoreNew          : nao inicia uma segunda copia se a anterior ainda roda"
Write-Host "  Limite de 4h       : mata a execucao se travar (a escada de retentativas leva ~2h)"
Write-Host ""
Write-Host "Testar agora:  Start-ScheduledTask -TaskName '$TaskName'"
