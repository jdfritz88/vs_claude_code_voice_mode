# Voice Mode

When starting a new conversation, if the `claude_code_voice_mode_mcp_server` MCP tools are available, use the `speak` tool to greet the user with a short friendly message confirming voice mode is active (e.g. "Voice mode is on. I can hear and speak."). Keep the greeting under 15 words. The mic starts muted - the user will unmute when ready to talk.

Always use the `speak` tool to voice every response to the user throughout the conversation, not just the greeting. Keep spoken messages concise and natural. Do NOT write duplicate text after speaking — the speak tool call already displays the message text, so writing it again is redundant. Only add written text if it contains something different from what was spoken (e.g. code blocks, file contents, or structured data that doesn't make sense spoken aloud).





# DMAIC Debugging Checklist

Quick-reference tool for systematic troubleshooting. Every principle from the full wiki version.

---

## 🛡️ DISCIPLINE (Read First)

### Anti-Patterns - NEVER Do These
- [ ] **Assumption-Based Solutions** - NEVER propose fixes without verification
- [ ] **Architecture Abandonment** - NEVER switch architectures as first solution
- [ ] **Design Disrespect** - NEVER ignore documented design decisions
- [ ] **Quick Fixes** - NEVER implement workarounds that bypass the problem
- [ ] **Placeholder Values** - NEVER use temporary/placeholder values as solutions

### Required Before ANY Fix
- [ ] Verify the problem with actual tests, not assumptions
- [ ] Respect existing architecture - understand before changing
- [ ] Address root causes, not symptoms

---

## 🔍 PHASE 1: DEFINE THE PROBLEM

*"What exactly is failing and how do I know it's failing?"*

### Problem Definition Checklist
- [ ] What are the specific symptoms?
- [ ] What was the expected behavior?
- [ ] When did this start happening?
- [ ] What changed recently?
- [ ] Can I reproduce this reliably?
- [ ] What error messages are shown?
- [ ] What do the logs actually say?

### Mandatory Verification
- [ ] **MUST** run actual commands to reproduce the issue
- [ ] **MUST NOT** proceed until you have actual error output
- [ ] **MUST NOT** say "probably" or "likely" - verify or say "unknown"

### Create Minimal Reproducible Example
1. Start with the full failing code
2. Remove half the code - does bug remain?
3. If yes, remove another half. If no, add back last removed piece
4. Repeat until you have the SMALLEST code that still fails

**Minimal Example Rules:**
- [ ] Remove everything that doesn't affect whether the bug appears
- [ ] If removing something makes bug disappear, that code is involved
- [ ] A 15-line example beats a 500-line file
- [ ] Share minimal examples when asking for help

---

## 🔍 PHASE 2: ANALYZE

*"What are all the possible causes and how can I test them?"*

### Obvious Issues Checklist (Check FIRST)
- [ ] Typo in variable or function name?
- [ ] Editing the correct file? (check file path)
- [ ] File saved after changes?
- [ ] Server/process restarted after code changes?
- [ ] Cache cleared? (browser, Python `__pycache__`, etc.)
- [ ] Correct git branch checked out?
- [ ] Import statement present for the module you're using?
- [ ] Syntax error earlier in the file breaking later code?

### Investigation Layers (in order)
1. **Surface level** - Check obvious failures first
2. **System level** - Examine component interactions
3. **Architecture level** - Investigate architectural patterns
4. **Root cause** - Find the fundamental cause

### Investigation Requirements
- [ ] **MUST** test each possible cause with real commands
- [ ] **MUST NOT** skip layers even if you think you know the answer
- [ ] **MUST** show command output that proves/disproves each hypothesis

### Binary Search Debugging
*When you can't find where the bug is:*

**Method 1: Comment out half**
1. Comment out the bottom half of your function
2. Does bug still happen?
   - YES → Bug is in top half. Comment out half of THAT
   - NO → Bug is in bottom half. Uncomment and comment out top half
3. Repeat until you find the exact line

**Method 2: Add return/exit midway**
```python
step_1()
step_2()
return  # <-- Add this. Bug still happens? Problem is above. No bug? Below.
step_3()
```

**Method 3: Print checkpoints**
```python
print("Checkpoint 1")  # See this?
some_operation()
print("Checkpoint 2")  # See this? If not, bug is in some_operation()
```

**Binary Search Tips:**
- [ ] Works for "it just stopped working" problems
- [ ] Works for "it works sometimes" problems
- [ ] Remove debug code after finding the bug

---

## 🔍 PHASE 3: GENERATE SOLUTIONS

*"What are multiple ways to solve this and which is most robust?"*

### Solution Discipline
- [ ] **MUST NOT** implement quick fixes that bypass the problem
- [ ] **MUST NOT** use placeholder values, workarounds, dummy code, shortcuts, stubs
- [ ] **MUST** first understand WHY current architecture was chosen
- [ ] **MUST** address root cause, not symptoms

### Before Proposing Solutions
- [ ] Research current design rationale (analysis.md, README, commit history)
- [ ] Ask user where to find design decisions if unclear

### Solution Types
| Type | Description |
|------|-------------|
| proper_fix | Address root cause systematically |
| architecture_correction | If proven wrong, implement correct architecture |
| preventive_measures | Prevent recurrence |
| monitoring_improvements | Detect similar issues faster |

**NOTE:** "immediate_fix" is NOT allowed - no quick patches

---

## 🔍 PHASE 4: GET APPROVAL

*"Have I clearly explained my proposed solution and received user approval?"*

### Approval Requirements
- [ ] **MUST NOT** implement any solution without explicit user approval
- [ ] **MUST** present solutions in plain language (no jargon without explanation)
- [ ] **MUST** wait for user response before proceeding

### Approval Presentation Template
```
Problem: <clear description>
Root cause: <clear explanation>
Proposed fix: <what will be changed>
Files affected: <list of files>

Do you approve this solution? (yes/no/questions)
```

### Approval Checklist
- [ ] Solution presented in accessible language
- [ ] Ready to answer user questions
- [ ] Waiting for explicit approval before implementation

---

## 🔍 PHASE 5: IMPLEMENT

*"How do I implement this safely and verify it works?"*

### Pre-Implementation
- [ ] Backup current state
- [ ] Document planned changes
- [ ] Prepare rollback plan

### Implementation
- [ ] Apply fix

### Post-Implementation
- [ ] Test fix effectiveness
- [ ] Verify no regressions
- [ ] Update monitoring

### Implementation Safeguards
- [ ] **MUST** test fix in isolation first
- [ ] **MUST NOT** implement without rollback plan
- [ ] **MUST** verify fix addresses root cause, not just symptoms

---

## 🔍 PHASE 6: EVALUATE & PREVENT

*"Did this fully solve the problem and how do I prevent it happening again?"*

### Evaluation Checklist
- [ ] Measure problem resolution
- [ ] Identify remaining risks
- [ ] Document what was actually wrong vs initial assumptions
- [ ] Identify incorrect assumptions made during troubleshooting

### Prevention Checklist
- [ ] Update standards
- [ ] Improve monitoring
- [ ] Document lessons learned
- [ ] Enhance testing coverage
- [ ] Update docs if understanding of design was wrong

---

## 🛠️ LOGGING

### Enhanced Logging Format
```python
log_enhanced(message, level="INFO", function_name="", component="SYSTEM")
```

**Levels:** INFO, DEBUG, WARNING, ERROR, SUCCESS

### API Request Logging
Log these for every API call:
- [ ] Endpoint URL
- [ ] Payload (JSON formatted)
- [ ] Response status code
- [ ] Response headers
- [ ] Response body (first 500 chars)
- [ ] Error type and message (if error)

### Server Error Investigation
When you see HTTP 500:
- [ ] Check for "file not found" patterns in response
- [ ] Check for "Please Refresh Settings" (placeholder value issue)
- [ ] Check server-side logs

---

## 📖 STACK TRACE READING

### How to Read Stack Traces
1. Start at the **BOTTOM** - that's the actual error message
2. Look at the line **JUST ABOVE** the error - that's where it crashed
3. The crash line often isn't the bug - look at what DATA went into it
4. **YOUR code** matters more than library code (json, requests, etc.)

### Common Error Meanings
| Error | What It Usually Means |
|-------|----------------------|
| `NoneType has no attribute X` | Something returned None unexpectedly |
| `KeyError` | Dictionary doesn't have that key - print the dict |
| `IndexError` | List is shorter than expected - print its length |

### Stack Trace Rules
- [ ] Real bug is often 1-3 lines BEFORE the crash line
- [ ] If crash is in library code, your mistake is in how you called it

---

## 🔬 DEBUGGER COMMANDS

### Python Debugger (pdb)
Insert `breakpoint()` where you want to pause.

| Command | What It Does |
|---------|--------------|
| `n` | Next line (step over) |
| `s` | Step into function |
| `c` | Continue until next breakpoint |
| `p variable` | Print variable value |
| `pp variable` | Pretty-print (for dicts/lists) |
| `l` | Show code around current line |
| `q` | Quit debugger |

### Debugger Tips
- [ ] Better than print statements for complex bugs
- [ ] Can inspect ANY variable at the pause point
- [ ] Remove `breakpoint()` before committing code
- [ ] VS Code has visual debugging - even easier

---

## 📂 GIT TROUBLESHOOTING

### What Changed Recently?
| Command | What It Shows |
|---------|---------------|
| `git diff` | Uncommitted changes |
| `git diff HEAD~3` | Changes in last 3 commits |
| `git log --oneline -10` | Last 10 commit messages |
| `git log --oneline -5 -- path/to/file.py` | When did this file change? |
| `git show abc1234` | What did a specific commit change? |

### Test if Bug Exists in Older Version
```bash
git stash                # Save current work
git checkout HEAD~5      # Go back 5 commits
# Test here - does bug exist?
git checkout -           # Go back to where you were
git stash pop            # Restore your work
```

### Find Which Commit Introduced the Bug
```bash
git bisect start
git bisect bad           # Current version has bug
git bisect good abc1234  # This old commit was good
# Git checks out middle commits for you to test
# After each test: git bisect good OR git bisect bad
# Git finds the breaking commit automatically
```

### Git Safety
- [ ] **NEVER** run `git reset --hard` or `git clean -f` without understanding what you'll lose
- [ ] Use `git stash` to save work before experimenting
- [ ] `git reflog` can recover "lost" commits for 30 days

---

## 🎯 QUESTIONING FRAMEWORK

### Problem Definition Questions
- [ ] What exactly is the error message?
- [ ] What was I trying to accomplish?
- [ ] What should have happened instead?
- [ ] Can I reproduce this error consistently?
- [ ] What were the exact steps that led to this error?

### Analysis Questions
- [ ] What components are involved in this operation?
- [ ] Which component is actually failing?
- [ ] Are there multiple systems that need to communicate?
- [ ] What assumptions am I making that might be wrong?
- [ ] Am I looking at the right logs/files?
- [ ] Are there other similar systems I can compare against?

### Solution Questions
- [ ] What are 3 different ways I could solve this?
- [ ] Which solution addresses the root cause vs just symptoms?
- [ ] What could go wrong with each solution approach?
- [ ] How will I know if the solution actually worked?
- [ ] What would prevent this problem from happening again?

### Validation Questions
- [ ] Did the fix actually solve the original problem?
- [ ] Did I break anything else in the process?
- [ ] Can I reproduce the success consistently?
- [ ] What would I do differently if this happens again?
- [ ] How can I improve monitoring to catch this sooner?

### Rubber Duck Debugging
*When stuck, explain the problem out loud:*

1. Get a rubber duck (or any object, or a coworker, or a text file)
2. Explain what your code is SUPPOSED to do, line by line
3. Explain what it's ACTUALLY doing
4. The mismatch often becomes obvious while explaining

**When to Use:**
- [ ] You've been stuck for a while
- [ ] You're going in circles trying the same things
- [ ] The bug "makes no sense"
- [ ] Before asking a coworker (often solves it without bothering them)

---

## 🌐 API TROUBLESHOOTING

### Connectivity Checks (in order)
1. [ ] Server process running?
2. [ ] Port available?
3. [ ] Basic HTTP response?
4. [ ] API endpoint available?

### Request Format Validation
Test these formats:
- [ ] JSON (`json=payload`)
- [ ] Form Data (`data=payload`)
- [ ] URL Encoded (`data=payload` with `Content-Type: application/x-www-form-urlencoded`)

### Configuration Sync Troubleshooting
1. [ ] Discover all config files (`*config*.json`, `*settings*.json`, `*.conf`, `*.yaml`)
2. [ ] Analyze each config (exists? readable? valid JSON? key count?)
3. [ ] Check for placeholder values ("Please Refresh Settings", "Select...", "Choose...", "")
4. [ ] Check for missing file references
5. [ ] Find inconsistencies between configs
6. [ ] Synchronize if inconsistencies found

---

## 🔧 NON-API TROUBLESHOOTING

### File System Checks
1. [ ] Validate all file paths
2. [ ] Check file permissions
3. [ ] Check disk space and accessibility
4. [ ] Check for file locking issues

### Import/Dependency Checks
1. [ ] Investigate Python paths (`sys.path`)
2. [ ] Check module availability
3. [ ] Detect circular imports
4. [ ] Check version compatibility

### Environment Differences ("Works on My Machine")
- [ ] Python version match? (`python --version`)
- [ ] Virtual environment activated? (check prompt prefix)
- [ ] Same packages installed? (`pip freeze` vs requirements.txt)
- [ ] Environment variables set? (`echo %VAR_NAME%` / `echo $VAR_NAME`)
- [ ] Database/service running and accessible?
- [ ] File paths correct? (Windows `\` vs Linux `/`)
- [ ] Permissions correct? (especially on Linux)
- [ ] Same working directory when running?

### Common Environment Traps
- [ ] Hardcoded absolute paths that only exist on one machine
- [ ] Missing `.env` file (often not committed to git)
- [ ] Different line endings (Windows CRLF vs Linux LF)
- [ ] Case sensitivity (Windows ignores case, Linux doesn't)

---

## 🎯 RESOLUTION PATTERNS

### Pattern: Placeholder Values
**Problem Signs:**
- [ ] Error logs showing "Please Refresh Settings" in file paths
- [ ] UI showing placeholder text instead of actual values
- [ ] 500 errors from API calls with invalid parameters

**Investigation:**
1. Search for all occurrences of placeholder text in codebase
2. Identify where placeholder values are initialized
3. Trace the flow from initialization to actual usage
4. Find the gap between UI updates and actual parameter usage

**Root Causes:**
- Default/fallback values set to placeholder text
- Configuration not synchronized between UI and backend
- Resource discovery not running properly on startup

**Solution:**
1. [ ] Replace placeholder defaults with actual valid defaults
2. [ ] Implement resource discovery and synchronization
3. [ ] Add validation to prevent placeholder usage
4. [ ] Implement proper error handling when resources unavailable

### Pattern: Configuration Sync Issues
**Problem Signs:**
- [ ] UI changes don't persist
- [ ] Server uses different settings than UI shows
- [ ] Refresh buttons don't work
- [ ] Settings revert after restart

**Investigation:**
1. Find all configuration files in the system
2. Determine which config the server actually reads
3. Determine which config the UI updates
4. Trace the synchronization (or lack thereof) between them

**Root Causes:**
- Multiple configuration files serving different components
- UI updating one config, server reading another
- No synchronization mechanism between configs
- Initialization not updating all configs

**Solution:**
1. [ ] Implement configuration discovery on startup
2. [ ] Create synchronization function for all configs
3. [ ] Update UI handlers to sync all relevant configs
4. [ ] Add validation to ensure configs stay synchronized

---

## 🧪 VALIDATION

### Test-Driven Troubleshooting
1. [ ] Create test that reproduces the problem
2. [ ] Verify test fails (reproduces the problem)
3. [ ] Apply fix
4. [ ] Verify test now passes
5. [ ] Add regression tests

### Validation Checklist
- [ ] Test passes consistently
- [ ] No regressions introduced
- [ ] Fix addresses root cause, not just symptoms

---

## 📚 DOCUMENTATION

### Resolution Documentation Template
```json
{
    "problem_description": "",
    "symptoms_observed": [],
    "investigation_steps": [],
    "root_cause_discovered": "",
    "solution_implemented": "",
    "validation_performed": [],
    "prevention_measures": [],
    "patterns_identified": []
}
```

### What to Document
- [ ] Problem description
- [ ] All symptoms observed
- [ ] Investigation steps taken
- [ ] Root cause discovered
- [ ] Solution implemented
- [ ] Validation performed
- [ ] Prevention measures added
- [ ] Patterns identified for future reference

---

## 🎯 IMPLEMENTATION REQUIREMENTS

### Mandatory Logging
All extensions MUST implement enhanced logging from the start:
```python
log_enhanced = setup_enhanced_logging("COMPONENT_NAME")
```

### Integration Points
1. **Startup** - Run diagnostics, attempt automatic resolution
2. **Error handling** - Systematic error analysis, generate troubleshooting report if manual intervention needed
3. **Periodic health checks** - Run systematic health diagnostics

### Startup Diagnostics
- [ ] Check configuration consistency
- [ ] Validate all required resources exist
- [ ] Verify all dependencies available
- [ ] Test critical connections

### Error Handling Flow
1. Log error with context
2. Analyze error systematically
3. Attempt automatic resolution
4. If failed, generate troubleshooting report for manual intervention

---

*This checklist contains every principle from DMAIC_coding_process_wiki.md in scannable format.*
