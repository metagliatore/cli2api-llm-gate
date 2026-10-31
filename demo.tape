Output docs/assets/demo.gif

Set FontSize 14
Set Width 860
Set Height 440
Set Theme "Catppuccin Mocha"
Set Padding 18
Set WindowBar Colorful
Set MarginFill "#181825"
Set BorderRadius 8
Set TypingSpeed 28ms

Hide
Type "alias curl='python3 /vhs/scripts/demo_stream.py #'"
Enter
Type "clear"
Enter
Show

Sleep 500ms
Type "curl -sN https://api.your-domain.com/v1/chat/completions \"
Enter
Sleep 120ms
Type "  -H 'Authorization: Bearer sk-antigravity-gate' \"
Enter
Sleep 120ms
Type "  -H 'Content-Type: application/json' \"
Enter
Sleep 120ms
Type "  -d '"
Type '{"model": "gemini-3.8-flash-high", "stream": true, "messages": [{"role": "user", "content": "Hi!"}]}'
Type "'"
Sleep 600ms
Enter

Sleep 3.5s
