# Kaigi TODO

## High-Priority Fixes & Improvements

### 1. Agent Communication Stability
**Problem:** Agent servers occasionally hang or timeout during IPC communication
**Impact:** Conversation workflow failures, especially with GLM agent
**Tasks:**
- [ ] Add heartbeat/ping mechanism to detect unresponsive agents
- [ ] Implement automatic agent restart on communication failure
- [ ] Add detailed logging for socket read/write operations
- [ ] Create integration test suite for agent server lifecycle

### 2. Conversation Memory Management (COMPLETED)
**Problem:** Long conversations may exceed agent context windows
**Impact:** Agent responses degrade or fail after extended discussions
**Status:** Implemented Hybrid-Rationale Strategy (Summary + Rolling Buffer)
- ✅ Implemented conversation summarization with configurable turn intervals
- ✅ Added truncation strategy (keep first N + last M turns)
- ✅ Store full conversation to disk, send summarized version to agents
- ✅ Added `--max-context-turns` flag to conversation config

### 3. Agent Quota & Usage Balancing
**Problem:** Agents have different usage limits (hourly vs monthly) and costs
**Impact:** High-usage agents (GLM) get blocked, while available agents (Copilot) sit idle
**Tasks:**
- [ ] Implement Global Usage Store (`~/.kaigi/usage_stats.json`)
- [ ] Add quota config to `agents.yaml` (period, limit, weight, cooldown)
- [ ] Implement Weighted Fair Queueing for agent selection
- [ ] Add `kaigi agents usage` command to view stats

### 4. Error Recovery in Execution Phase
**Problem:** If agent fails during execution, changes may be partially applied
**Impact:** Inconsistent codebase state, difficult rollback
**Tasks:**
- [ ] Create backup/snapshot before execution phase begins
- [ ] Implement transaction log of file changes during execution
- [ ] Add `kaigi rollback <conversation-id>` command
- [ ] Test recovery from mid-execution failures

## Medium-Priority Enhancements

### 5. Consensus Detection Improvements
**Problem:** Current keyword-based consensus ("AGREED:") is brittle
**Impact:** May miss implicit agreement or trigger on false positives
**Tasks:**
- [ ] Add sentiment analysis to detect agreement without exact keyword
- [ ] Require structured consensus format: `AGREED: [summary of decision]`
- [ ] Allow configurable consensus threshold (e.g., 2/3 agents instead of all)
- [ ] Add `--consensus-mode` flag: strict (all), majority, lead-only

### 6. Agent Configuration Validation
**Problem:** Invalid agent configs cause runtime errors during conversation
**Impact:** Wasted time debugging YAML syntax or missing fields
**Tasks:**
- [ ] Add `kaigi agents validate` command
- [ ] Check agent command accessibility before starting conversation
- [ ] Validate persona/model compatibility (warn if model doesn't support persona)
- [ ] Pre-flight check in `kaigi converse` before starting agents

### 7. Workflow Templates & Examples
**Problem:** Users need to write YAML workflows from scratch
**Impact:** Steep learning curve, copy-paste errors
**Tasks:**
- [ ] Create `kaigi init --template <name>` for common patterns
- [ ] Add templates: code-review, feature-discussion, architecture-design
- [ ] Generate workflow from natural language: `kaigi generate "review PR #123"`
- [ ] Add workflow validation with helpful error messages

## Low-Priority / Future Ideas

### 8. Web UI for Conversation Monitoring
**Problem:** CLI-only interface limits visibility into multi-agent discussions
**Impact:** Hard to follow conversations in real-time, especially for observers
**Tasks:**
- [ ] Create simple web server: `kaigi serve --port 8080`
- [ ] Show live conversation view with agent avatars/colors
- [ ] Display agent status (thinking, typing, waiting)
- [ ] Allow web-based user input for interactive conversations

### 9. Agent Performance Analytics
**Problem:** No metrics on agent response quality or consensus efficiency
**Impact:** Can't optimize agent selection or conversation strategies
**Tasks:**
- [ ] Track metrics: time-to-consensus, turns-per-conversation, agent response times
- [ ] Generate report: `kaigi stats <conversation-id>`
- [ ] Identify patterns: which agent combinations reach consensus fastest?
- [ ] Add `--profile` flag to log detailed performance data

### 10. Integration with External Tools
**Problem:** Agents can't access external context (GitHub issues, docs, databases)
**Impact:** Limited to codebase knowledge, can't fetch runtime data
**Tasks:**
- [ ] Add tool/plugin system for agents to call external APIs
- [ ] Create plugins: github (fetch issues/PRs), docs (search documentation)
- [ ] Allow agents to request tool execution: `TOOL: github.get_issue(123)`
- [ ] Sandbox tool execution with user approval for sensitive operations

### 11. Conversation Branching & Replay
**Problem:** Can't explore alternative discussion paths or replay with different agents
**Impact:** One-shot conversations, no experimentation
**Tasks:**
- [ ] Add `kaigi branch <conversation-id> --from-turn N`
- [ ] Allow conversation replay with different agent configurations
- [ ] Compare outcomes across branches (A/B testing for agent teams)
- [ ] Merge insights from multiple branches back to main conversation

---

## Testing Priorities

### Critical Test Coverage Gaps
1. **Agent server crash scenarios** - What happens when agent dies mid-conversation?
2. **Network partition simulation** - How does IPC handle slow/broken sockets?
3. **Concurrent conversation handling** - Can multiple workflows run simultaneously?
4. **Large file execution** - Does execution phase handle big diffs correctly?
5. **Edge cases in consensus** - Empty messages, malformed AGREED statements, etc.

### Recommended Test Framework Additions
- Hypothesis-based property testing for conversation state transitions
- Chaos engineering: randomly kill agents, corrupt sockets, delay messages
- Performance benchmarks: 10-turn conversation should complete in <30s

---

## Documentation Improvements

1. **Architecture diagram** - Visual overview of agent servers, IPC, conversation flow
2. **Troubleshooting guide** - Common errors and solutions (agent won't start, timeout, etc.)
3. **Best practices** - When to use team vs orchestrated mode, agent selection tips
4. **Migration guide** - How to upgrade from sequential workflows to conversations
5. **API reference** - Document all CLI commands, flags, and YAML schema

---

## Notes on Prioritization

**Priority 1** items (1-4) directly address stability and reliability. These should be completed before any public release.

**Priority 2** items (5-7) improve user experience and reduce friction. Consider these for v1.1 after core stability is proven.

**Priority 3** items (8-11) are innovative but not essential. Good candidates for community contributions or experimental branches.

**Test coverage** should happen in parallel with Priority 1 fixes - write tests that reproduce bugs before fixing them.

---

*This TODO reflects consensus from the architecture, review, and analyst perspectives. Update as priorities shift or new issues emerge.*
