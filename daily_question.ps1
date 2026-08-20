# Daily Question & View Generator
# 运行此脚本开始问题驱动对话

Write-Host '=== Daily Question & View Generator ===' -ForegroundColor Cyan
Write-Host '1. Generate questions after analysis' -ForegroundColor Green
Write-Host '2. Start interactive dialogue (type your idea)' -ForegroundColor Green
Write-Host '3. Or read draft from Obsidian idea folder' -ForegroundColor Gray

Write-Host ''
Write-Host 'Choose mode: 1-interactive, 2-batch, 3-generate-questions' -ForegroundColor Yellow
 = Read-Host 'Enter mode'

if ( -eq '1') {
    Write-Host 'Enter your idea (press Enter when done):' -ForegroundColor White
     = Read-Host
    if () {
        # Run Python dialogue
        python -c "
import sys
sys.path.insert(0, r'C:\Users\admin\Documents\osint\local')
from dialogue_engine import DialogueEngine
import yaml
with open(r'C:\Users\admin\Documents\osint\config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)
engine = DialogueEngine(config, None)
engine.run_interactive('')
"
    }
} elseif ( -eq '2') {
    Write-Host 'Enter path to Obsidian idea draft:' -ForegroundColor White
     = Read-Host
    if (Test-Path ) {
        python -c "
import sys
sys.path.insert(0, r'C:\Users\admin\Documents\osint\local')
from dialogue_engine import DialogueEngine
engine = DialogueEngine(None, None)
engine.run_batch(r'\')
"
        }
} elseif ( -eq '3') {
    Write-Host 'Generating 3-5 questions after analysis...' -ForegroundColor Green
    python -c "
import sys
sys.path.insert(0, r'C:\Users\admin\Documents\osint\local')
from question_generator import QuestionGenerator
with open(r'C:\Users\admin\Documents\osint\config.yaml', 'r', encoding='utf-8') as f:
    import yaml
    config = yaml.safe_load(f)
gen = QuestionGenerator(config)
questions = gen.generate_daily_questions('Today\'s analysis: CPI up 1.8%, PPI down 0.5%, youth unemployment at 18%')
for q in questions:
    print(f'Round {q[\"round\"]}: {q[\"prompt\"]}')
"
}