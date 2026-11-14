# Learner guide

Your organizer provides the workshop URL, your username and temporary password,
the selected course, its expiry and a support contact. Use your own account.
Do not send your password or a progress export in a public chat.

## Sign in and choose the correct activity

Open the supplied HTTPS activity entry, sign in and follow the first-login
password-change prompt. If the organizer requires another authentication step,
follow the displayed instructions. You do not need AWS credentials or a local
Docker installation to use this environment.

Check the displayed workshop and learner ID before opening tools. Two courses
can appear in the shared activity list. Each course has its own login and its
own machine; opening the same learner name in another activity does not merge
accounts, sessions or data. Keep each course in its own tabs.

## Read, run and save your work

Use **Courses & Labs** for the selected lessons, images and quiz instructions.
Open a Notebook with that course's kernel. Use the provided Web Terminal for
shell and Docker commands. Open Doris FE, Superset, Flink or Observability tools
from your own activity's links; container ports and another learner's URLs are
not alternative browser entries.

Wait for the Notebook kernel to be idle before assuming an operation finished.
Save the Notebook and download a copy when you want to keep its code and output.
A downloaded Notebook is a file backup, not a backup of database tables,
containers, volumes or an entire Lab. Tell the organizer if the displayed
course/learner identity is wrong or a selected service never becomes ready.

## Keep and transfer your learning record

The course outline keeps the available module reading, Lab self-assessment and
quiz completion record on this environment. The displayed record is personal
to the account on this activity; shared credentials share the same record.
It is not a formal grade or website completion certificate.

Before the activity ends, export the progress JSON from the course interface.
On a later compatible activity, choose import, select that file and review the
preview. Confirm only after the matching course/modules are correct. Unknown
or unselected modules are skipped. Preview alone does not import the record.
Import merges the supported learning record; it does not restore Notebooks,
databases, running services or the old Lab's files.

Keep the exported version that contains your full history. Follow the preview
when importing another supported format or language. An older image may not
contain this feature; the organizer's declared release determines availability.

## Reconnect, sign out and finish

If a connection or login expires, follow the reauthentication prompt and reopen
your course's tool. Refreshing a browser does not restore a terminated machine.
If a kernel or terminal remains unavailable, report the activity/learner ID,
tool and approximate failure time to the organizer. Do not paste tokens,
passwords or private Notebook data into the report.

**Sign out of this entry** signs out that entry. Already authenticated service
origins may have their own sessions, and the other course has a separate login.
On a shared computer, end your service sessions and close your browser session
when finished. Do not assume one entry button signs out every tab or course.

Download the Notebooks, other files and progress JSON you want to retain before
expiry. Ending or replacing your environment deletes its temporary disk,
including Lab data and the local progress store. There is no automatic cloud
backup or automatic transfer to the next workshop.
