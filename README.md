# Secret Santa (privacy-safe)

Lightweight Secret Santa matcher with spouse + recent-history constraints and an optional email sender. Templates are included so real names, emails, and credentials stay local.

## Quick start
1) Copy and fill templates (safe to edit locally; ignored by git):
```bash
cp examples/people.example.txt people.txt
cp examples/emails.example.txt emails.txt
cp examples/sendercredentials.example.txt sendercredentials.txt
```
2) Generate assignments (avoids gifting the past 3 years by default):
```bash
python Secret_santa.py --data-dir . --output-dir .
```
3) Preview emails (dry-run by default):
```bash
python SendEmails.py
```
4) When ready to send, set `send = true` and add your app password in `sendercredentials.txt`, then rerun `python SendEmails.py`.

## Running a second exchange (e.g. kids)
Keep each group in its own folder so history and year files don't collide:
```bash
mkdir kids   # add kids/people.txt, kids/emails.txt, kids/sendercredentials.txt
python Secret_santa.py --people kids/people.txt --data-dir kids --output-dir kids
python SendEmails.py kids/sendercredentials.txt
```
`emails.txt` can map several names to the same address (e.g. a parent's). Relative `assignments_dir`/`emails_file` paths resolve from the credentials file's folder (default: that folder).

## Files and formats
- `people.txt`: `Name[, Excluded, ...]` per line — everyone after the first name (spouse, siblings) can't be drawn by that person, and vice versa. Comments start with `#`.
- `20YY.txt`: generated assignments `Giver -> Receiver` (see `examples/assignments/2024-sample.txt`).
- `emails.txt`: `Name, email` per line; names must match the assignment file.
- `sendercredentials.txt`: SMTP settings (`smtp_server`, `smtp_port`, `username`, `password`, optional `from`, `subject`, `wishlist_url`, `note`, `assignments_dir`, `emails_file`, `wishlist_doc`, `wishlist_aliases`).
- Wish list doc (optional, `wishlist_doc = Gift Exchange.docx`): a Word doc with a name heading per person, then `Where to send gift: <address>` and link lines (`Gift list link:`, `Amazon:`, `Other:`). Each giver's email gets their receiver's address and list links (hyperlinks expanded to URLs). Headings match full names or first names; map others with `wishlist_aliases = Brian=Dad, Jennifer=Mom`. Dry-run warns about anyone missing an address or list.

## CLI options (generator)
- `--people`: path to people file (default `people.txt`)
- `--years`: avoid gifting the same person within N past years (default 3)
- `--data-dir`: where past `20YY.txt` files live (default current dir)
- `--output-dir`: where to write the new `YYYY.txt` (default current dir)
- `--year`: override output year (default system year)
- `--seed`: set RNG seed for reproducible draws
- `--max-tries`: backtracking attempts (default 500)

## Email sender behavior
- Finds the most recent `YYYY.txt` in `assignments_dir` (default: alongside the script).
- Builds emails for givers found in both the assignments file and `emails.txt`.
- Stays in dry-run mode until `send = true`. Requires SMTP app password when sending.
- Optional `wishlist_url` and `note` fields get appended to the email body.

## Privacy and repo hygiene
- `.gitignore` excludes personal data, credentials, generated year files, and `OldCode/`.
- Keep your real files (`people.txt`, `emails.txt`, `sendercredentials.txt`, `20YY.txt`) untracked or in `private/`.
- Commit only the scripts and `examples/` folder.

## Requirements
- Python 3.10+.
- Network access only needed when sending emails. If no valid assignment is found, relax constraints or add participants.
