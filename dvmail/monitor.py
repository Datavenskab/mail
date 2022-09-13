import os
import sys
import json
import time
import email
import logging
import argparse
import textwrap
import smtplib

from emailtunnel import Message, decode_any_header, logger
from tkmail.delivery_reports import parse_delivery_report
import tkmail.headers

try:
    from tkmail.address import get_admin_emails
except ImportError:
    print("Cannot import tkmail.address; stubbing out get_admin_emails")
    get_admin_emails = lambda: ['mathiasrav@gmail.com']


MAX_SIZE = 10
MAX_DAYS = 2


def configure_logging(use_tty):
    if use_tty:
        handler = logging.StreamHandler(None)
    else:
        handler = logging.FileHandler('monitor.log', 'a')
    fmt = '[%(asctime)s %(levelname)s] %(message)s'
    datefmt = None
    formatter = logging.Formatter(fmt, datefmt, '%')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


def get_report(basename):
    with open('error/%s.json' % basename) as fp:
        metadata = json.load(fp)

    try:
        with open('error/%s.mail' % basename, 'rb') as fp:
            message = email.message_from_binary_file(fp)
        report = parse_delivery_report(message)
        if report:
            metadata['summary'] = report.notification
            metadata['subject'] = str(decode_any_header(
                report.message.get('Subject', '')))
    except Exception:
        logger.exception('parse_delivery_report failed')

    mtime = os.stat('error/%s.txt' % basename).st_mtime

    report = dict(metadata)
    report['mtime'] = int(mtime)
    report['basename'] = basename
    return report


def archive_report(basename):
    for ext in 'txt json mail'.split():
        filename = '%s.%s' % (basename, ext)
        try:
            os.rename('error/%s' % filename, 'errorarchive/%s' % filename)
        except Exception:
            logger.exception('Failed to move %s' % filename)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', '--dry-run', action='store_true')
    args = parser.parse_args()

    configure_logging(args.dry_run)

    try:
        filenames = os.listdir('error')
    except OSError:
        filenames = []

    now = int(time.time())
    oldest = now
    reports = []

    for filename in sorted(filenames):
        if not filename.endswith('.txt'):
            continue

        basename = filename[:-4]

        try:
            report = get_report(basename)
        except Exception:
            exc_value = sys.exc_info()[1]
            logger.exception('get_report failed')
            report = {
                'subject': '<get_report(%r) failed: %s>'
                           % (basename, exc_value),
                'basename': basename,
            }
        else:
            oldest = min(oldest, report['mtime'])

        reports.append(report)

    age = now - oldest

    logger.info('%s report(s) / age %s (limit is %s / %s)' %
                (len(reports), age, MAX_SIZE, MAX_DAYS * 24 * 60 * 60))

    if (not args.dry_run and (len(reports) <= MAX_SIZE and
                              age <= MAX_DAYS * 24 * 60 * 60)):
        return

    admins = get_admin_emails()

    # admins = ['mathiasrav@gmail.com']

    keys = 'mailfrom rcpttos subject date summary mtime basename'.split()

    for report in reports:
        try:
            mailfrom = report['mailfrom']
        except KeyError:
            continue
        # Sometimes the sender domain names are spam domains
        # which can probably cause Google to block the email
        # report. Mask sender domain names to avoid this.
        report['mailfrom'] = mailfrom.replace('.', ' ').replace('@', ' ')

    lists = {}
    for k in keys:
        if k == 'rcpttos':
            sort_key = lambda x: tuple(x[1].get(k) or [])
        else:
            sort_key = lambda x: x[1].get(k) or ''
        lists[k] = '\n'.join(
            '%s. %s' % (i + 1, report.get(k))
            for i, report in sorted(
                enumerate(reports),
                key=sort_key
            ))

    # "Subject" section removed Apr 20, 2017 since Google blocked
    # one of these report emails for having spam content, likely due
    # to a very spammy-looking subject line in the body.

    body = textwrap.dedent("""
    This is the mail system of TAAGEKAMMERET.

    The following emails were not delivered to anyone.

    Reasons for failed delivery:
    {lists[summary]}

    Senders:
    {lists[mailfrom]}

    Recipients:
    {lists[rcpttos]}

    Sent:
    {lists[date]}

    Received:
    {lists[mtime]}

    Local reference in errorarchive folder:
    {lists[basename]}
    """).format(lists=lists)

    sender = recipient = 'admin@TAAGEKAMMERET.dk'
    message = Message.compose(
        sender, recipient, '[TK-admin] Failed email delivery', body)

    headers = tkmail.headers.get_extra_headers(sender, 'tkmailmonitor',
                                               is_group=True)
    for k, v in headers:
        message.add_header(k, v)

    if args.dry_run:
        print("Envelope sender: %r" % sender)
        print("Envelope recipients: %r" % admins)
        print("Envelope message:")
        print(str(message))
        return

    relay_hostname = '127.0.0.1'
    relay_port = 25
    relay_host = smtplib.SMTP(relay_hostname, relay_port)
    relay_host.set_debuglevel(0)

    try:
        relay_host.sendmail(sender, admins, str(message))
    finally:
        try:
            relay_host.quit()
        except smtplib.SMTPServerDisconnected:
            pass

    # If no exception was raised, the following code is run
    for report in reports:
        archive_report(report['basename'])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info('monitor exited via KeyboardInterrupt')
        raise
    except SystemExit:
        raise
    except:
        logger.exception('monitor exited via exception')
        raise
