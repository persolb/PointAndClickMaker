# UTIL_DISPATCH

- SCN-042 / DG_SCN_042_ALARM_PANEL / N_DISPATCH_CALL
  Control Room, this is Utility Dispatch. Timestamp 14:17:09.
  We’re issuing a load-shedding request for your node. Confirm receipt and give me your action record ID.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_ACK_UTIL
  Acknowledged at 14:17:22. Provide your action record ID.
  And confirm you will maintain stability. I need that stated, not implied.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_AUTH_UTIL
  Accepted. Timestamp 14:17:26.
  Provide the record ID when available. Report shed amount and start time when you act.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_MAINTAIN_UTIL
  Good. Timestamp 14:17:31.
  Constraint: do not introduce oscillatory loading. If you don’t know what’s oscillatory on your side, assume it is.
  Call back with shed amount and start time.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_DEFINE_UTIL
  Stability means your node does not create a problem I have to name.
  Voltage within band, frequency support unchanged, no step-load chatter, no cycling.
  And—because it comes up—no undocumented behavior. That’s not technical. That’s legal.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_UNDERSTOOD_UTIL
  Timestamp 14:17:58. Accepted.
  Call when the first shed is complete. Don’t wait for perfection.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_TARGET_UTIL
  Target is twelve megawatts reduction at the node. Duration unknown—minimum twenty minutes, reassess at the half-hour mark.
  Do not shed anything that causes a restart surge. If it “auto-recovers,” it’s not a shed.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_COMMIT_DIRECT_UTIL
  Timestamp 14:18:06. Logged.
  I still need your action record ID. If you can’t generate one, give me the printer header serial. Something attributable.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_DETAILS_UTIL
  Good. Timestamp 14:17:12.
  Target is twelve megawatts reduction at the node, duration unknown—minimum twenty minutes, reassess at the half-hour mark.
  Do not shed anything that causes a restart surge. If it “auto-recovers,” it’s not a shed.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_DETAILS_ACK_UTIL
  Timestamp 14:17:26. Logged.
  I still need your action record ID. If you can’t generate one, give me the printer header serial. Something attributable.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_STAGED_UTIL
  Prefer staged if it does not introduce chatter. Avoid cycling loads.
  First action should be clean and attributable. Then we reassess at the half-hour mark.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_STAGED_COMMIT_UTIL
  Timestamp 14:18:14. Logged.
  Proceed. Call when the first step is complete.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_NOTICE_UTIL
  It’s Demand Event UTIL-DS-4419. Timestamp 14:17:15.
  And for clarity: this is a directive under the stability clause. Not a suggestion.
  Confirm you are acting.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_CONF_UTIL
  Timestamp 14:17:24. Logged.
  Provide record ID when available. I’ll hold the line open for two minutes. After that, call back—don’t just disappear.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_BARGAIN_UTIL
  It is in writing. It’s recorded, timestamped, and mirrored to the daily log.
  If you mean an email: you’ll get it when the system feels like it.
  Confirm action now.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_PRINT_UTIL
  Control Room, confirm receipt. Timestamp 14:17:19.
  And give me your record ID.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_READBACK_UTIL
  Received. Timestamp 14:17:28.
  That’s attributable. Proceed with shed. Report first action time.
- SCN-042 / DG_SCN_042_ALARM_PANEL / N_PRINT_ACK_UTIL
  Logged. Timestamp 14:17:33.
  Confirm you will maintain stability. And provide a record ID when available.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_DISPATCH_OPEN
  Control Room, Utility Dispatch. Timestamp 14:22:04.
  Confirm you are acting on the demand event. And I need your action record ID when you have it.
  Also—state you will maintain stability. Stated, not assumed.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_AUTH_UTIL
  Accepted. Timestamp 14:22:11.
  Provide the record ID when available. Report shed start time when the first action occurs.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_THREAT_PASS_UTIL
  Noted. Timestamp 14:22:14.
  Directive stands. Execute shed now, avoid cycling and step-load chatter. Provide record ID and shed start time.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_THREAT_FAIL_UTIL
  Negative. Timestamp 14:22:16.
  Directive is documented via recording and log mirror. Confirm action now. Do not delay shed for process language.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_GROUP_UTIL
  And to be clear: “auto-recovers” is not a shed. It’s a delay.
  Avoid cycling. Avoid chatter. Give me something stable and attributable.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_DEFINE_UTIL
  Stability means your node does not create a problem I have to name. Voltage in band. No step-load chatter. No cycling.
  And—because it comes up—no undocumented behavior. That’s not technical. That’s legal.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_STAGED_UTIL
  Confirm you’re acting. I don’t need your philosophy. I need a time stamp and a stable response.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_STAGED_CONFIRM_UTIL
  Timestamp 14:22:31. Logged.
  Provide action record ID when available. Report first step time when it occurs.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_POST_ACK_UTIL
  Timestamp 14:22:57. Confirm you are acting and will maintain stability.
- SCN-043 / DG_SCN_043_MAIN_CONSOLE / N_READBACK_UTIL
  Received. Timestamp 14:22:19.
  Proceed. Report shed start time when the first action occurs.
- SCN-074 / DG_SCN074_CLOSURE_MEMO / N_STABLE_UD1
  I need stability stated, not implied.
  If it’s not attributable, it didn’t happen—legally.
- SCN-074 / DG_SCN074_CLOSURE_MEMO / N_STABLE_UD2
  Everything becomes a control log when someone’s looking for a cause.
  Include time anchors. Include record IDs. Include that you will not introduce oscillatory loading.
  And—this is procedural—don’t make it sound like you “couldn’t.” Make it sound like you “did.”
