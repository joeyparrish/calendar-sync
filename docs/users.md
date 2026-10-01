# Using Calendar Sync

Calendar Sync adds private "busy" blocks to your work calendar whenever you are
busy on another calendar. Coworkers see that you are busy. Only you see the
block labels and colors.

## Sign in

Open your organization's Calendar Sync address and sign in with your work
Google account. Allow the permissions Google asks for.

## Add a Google calendar

1. In the other account's Google Calendar, open **Settings**, then the calendar
   under **Settings for my calendars**. Either:
   - under **Share with specific people or groups**, add your work address with
     **See only free/busy (hide details)**, or
   - under **Access permissions for events**, make it available to the public
     with **See only free/busy (hide details)**.
2. In Calendar Sync, use **Add a Google calendar**: enter that calendar's email
   address, a label, and a color.

Some organizations block sharing outside the company. If the source shows an
error such as `notFound`, the sharing did not work.

## Add Outlook (or any ICS link)

1. In Outlook on the web, open **Settings**, **Calendar**, **Shared calendars**.
   Under **Publish a calendar**, choose your calendar and **Can view when I'm
   busy**, then **Publish**. Copy the ICS link.
2. In Calendar Sync, use **Add an ICS link**: paste the link, then add a label
   and a color.

Treat the ICS link like a password. Calendar Sync only ever shows its host
after it is added.

## What you see

- Blocks are titled `busy (label)` and marked private.
- **Edit** changes a source's label or color; existing blocks update on the
  next sync.
- **Destination calendar** picks which of your calendars gets the blocks.
  Switching removes upcoming blocks from the old one.

## Syncing

Calendar Sync syncs every 10 minutes. **Sync now** syncs right away. If a
source fails, its blocks stay put and the error shows in its row.

## Stopping

**Remove my data** deletes your upcoming blocks, your settings, and Calendar
Sync's access to your account.
