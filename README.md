# Calendar Sync

Do you have multiple calendars running your life?  Multiple jobs?  How can you
make sure your colleagues with access to one account don't book you when you're
busy with something in your personal calendar, or in your other job?

This tool copies busy blocks from your other calendars (Google, or any option
with ICS, including Outlook) to your work calendar on Google Workspace, so
coworkers can see when you are available.

It is hosted on Google Cloud Run and deployed under the target organization as
an internal tool.  End users sign in with their Workspace accounts on a web UI,
and add calendars they want to read "busy" times from.  For more details, see
the [setup](docs/operators.md) and [end-user](docs/users.md) docs.


## License

Licensed under the GPLv3, or (at your option) any later version.
See [LICENSE.md](LICENSE.md).

