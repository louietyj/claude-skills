# Two Windows voices act out a short consultation, so the relay can be tested
# against real Deepgram with a known script: two speakers to diarize, keyterms
# to land, pauses for endpointing. Writes dev/sample.webm (opus, like the page).
#
#   powershell -File dev/make_sample.ps1
Add-Type -AssemblyName System.Speech
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wav = Join-Path $here "sample.wav"
$out = Join-Path $here "sample.webm"

$lines = @(
  @("David", "So the trust would pay for your parents' medical bills, but not their rent?"),
  @("Zira",  "Right. I want the Wooga Family Trust to cover medical and education only."),
  @("David", "And what happens if they give their own money away first?"),
  @("Zira",  "Then the trustee should apply more scrutiny before paying anything out."),
  @("David", "A court in Singapore may not enforce a punitive clause like that."),
  @("Zira",  "Okay. Let me talk to my parents before I sign anything.")
)

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile($wav)
$prompt = New-Object System.Speech.Synthesis.PromptBuilder
foreach ($l in $lines) {
  $prompt.StartVoice("Microsoft $($l[0]) Desktop")
  $prompt.AppendText($l[1])
  $prompt.EndVoice()
  $prompt.AppendBreak([TimeSpan]::FromMilliseconds(1200))
}
$synth.Speak($prompt)
$synth.Dispose()

& ffmpeg -y -loglevel error -i $wav -ac 1 -ar 48000 -c:a libopus -b:a 32k $out
Remove-Item $wav
Write-Output "wrote $out"
