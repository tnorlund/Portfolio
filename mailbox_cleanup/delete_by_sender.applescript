-- Move messages from a given sender to Trash in Mail.app.
-- Mail.app's `delete` moves to the account Trash (reversible) -- it does NOT
-- permanently erase. Emptying Trash is a separate, manual step.
--
-- Usage:
--   osascript delete_by_sender.applescript count  "news@example.com"   -> just counts
--   osascript delete_by_sender.applescript delete "news@example.com"   -> trashes them
--
-- Matching is a substring test on the sender field (display name + address),
-- case-insensitive, so "example.com" nukes a whole domain.

on run argv
	if (count of argv) < 2 then
		return "usage: osascript delete_by_sender.applescript <count|delete> <sender>"
	end if
	set theMode to item 1 of argv
	set theSender to item 2 of argv
	set movedCount to 0
	set matchedCount to 0

	tell application "Mail"
		-- `inbox` is the unified inbox across all accounts.
		set matched to (every message of inbox whose sender contains theSender)
		set matchedCount to count of matched
		if theMode is "delete" then
			repeat with m in matched
				try
					delete m
					set movedCount to movedCount + 1
				end try
			end repeat
		end if
	end tell

	return theSender & tab & "matched=" & (matchedCount as text) & tab & "trashed=" & (movedCount as text) & tab & "mode=" & theMode
end run
