import os
import re
import sys
import json
import datetime
import textwrap
import itertools
import traceback
from collections import namedtuple, OrderedDict

import email.header
from tkmail.util import DecodingDecodedGenerator

from emailtunnel import (
    SMTPForwarder, Message, InvalidRecipient, Envelope, logger,
)

import tkmail.address

from tkmail.address import (
    GroupAlias,
    # PeriodAlias, DirectAlias,
)
from tkmail.dmarc import has_strict_dmarc_policy
from tkmail.delivery_reports import parse_delivery_report


RecipientGroup = namedtuple(
    'RecipientGroup', 'origin recipients'.split())


def now_string():
    """Return the current date and time as a string."""
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")


class TKForwarder(SMTPForwarder):
    MAIL_FROM = 'admin@TAAGEKAMMERET.dk'

    ERROR_TEMPLATE = """
    This is the mail system of TAAGEKAMMERET.

    The following exception was raised when processing the message below:

    {traceback}

    This exception will not be reported again before the mail server has
    been restarted.

    Envelope sender: {mailfrom}
    Envelope recipients: {rcpttos}
    Envelope message:

    {message}
    """

    ERROR_TEMPLATE_CONSTRUCTION = """
    This is the mail system of TAAGEKAMMERET.

    The following exception was raised when CONSTRUCTING AN ENVELOPE:

    {traceback}

    This exception will not be reported again before the mail server has
    been restarted.

    Raw data:

    {data}
    """

    def __init__(self, *args, **kwargs):
        self.year = kwargs.pop('year')
        self.exceptions = set()
        self.delivered = 0
        self.deliver_recipients = {}
        super(TKForwarder, self).__init__(*args, **kwargs)

    def startup_log(self):
        logger.info(
            'TKForwarder listening on %s:%s, relaying to %s:%s, GF year %s',
            self.host, self.port, self.relay_host, self.relay_port, self.year)

    def log_receipt(self, peer, envelope):
        mailfrom = envelope.mailfrom
        message = envelope.message

        if type(mailfrom) == str:
            sender = '<%s>' % mailfrom
        else:
            sender = repr(mailfrom)

        try:
            recipients_header = OrderedDict()
            for address, formatted, header in envelope.recipients():
                if address is not None:
                    address = re.sub(r'@(T)AAGE(K)AMMERET\.dk$', r'@@\1\2',
                                     address, 0, re.I)
                    recipients_header.setdefault(header, []).append(address)
            recipients = ' '.join(
                '%s: <%s>' % (header, '>, <'.join(group))
                for header, group in recipients_header.items())
        except Exception as exn:
            logger.exception('Envelope.recipients() processing failed')
            rcpttos = envelope.rcpttos
            if type(rcpttos) == list and all(type(x) == str for x in rcpttos):
                rcpttos = [re.sub(r'@(T)AAGE(K)AMMERET\.dk$', r'@@\1\2',
                                  address, 0, re.I)
                           for address in rcpttos]
                if len(rcpttos) == 1:
                    recipients = '<%s>' % rcpttos[0]
                else:
                    recipients = ', '.join('<%s>' % x for x in rcpttos)
            else:
                recipients = repr(rcpttos)
            recipients = 'To: ' + recipients

        logger.info("Subject: %r From: %s %s",
                    str(message.subject), sender, recipients)

    def log_delivery(self, message, recipients, sender):
        if all('@' in rcpt for rcpt in recipients):
            parts = [rcpt.split('@', 1) for rcpt in recipients]
            parts.sort(key=lambda x: (x[1].lower(), x[0].lower()))
            by_domain = [
                (domain, [a[0] for a in aa])
                for domain, aa in itertools.groupby(
                    parts, key=lambda x: x[1])
            ]
            recipients_string = ', '.join(
                '<%s@%s>' % (','.join(aa), domain)
                for domain, aa in by_domain)
        else:
            recipients_string = ', '.join('<%s>' % x for x in recipients)

        if len(recipients_string) > 200:
            age = self.deliver_recipients.get(recipients_string)
            if age is None or self.delivered - age > 40:
                self.deliver_recipients[recipients_string] = self.delivered
                recipients_string += ' [%d]' % self.delivered
            else:
                recipients_string = '%s... [%d]' % (
                    recipients_string[:197], age)

        self.delivered += 1

        logger.info('Subject: %r To: %s',
                    str(message.subject), recipients_string)

    def handle_delivery_report(self, envelope):
        if envelope.mailfrom != '<>':
            return
        rcpttos = tuple(r.lower() for r in envelope.rcpttos)
        if rcpttos != ('admin@taagekammeret.dk',):
            return
        report = parse_delivery_report(envelope.message.message)
        if not report:
            return
        original_mailfrom = report.message.get('Return-Path') or '(unknown)'
        inner_envelope = Envelope(Message(report.message), original_mailfrom,
                                  report.recipients)
        description = summary = report.notification
        self.store_failed_envelope(envelope, description, summary,
                                   inner_envelope)
        logger.info('%s', summary)
        return True

    def reject(self, envelope):
        # Reject delivery status notifications not sent to admin@
        try:
            content_type = envelope.message.get_unique_header('Content-Type')
        except KeyError:
            content_type = ''
        ctype_report = content_type.startswith('multipart/report')
        ctype_delivery = 'report-type=delivery-status' in content_type
        if ctype_report and ctype_delivery:
            return 'Content-Type looks like a DSN'

        rcpttos = tuple(r.lower() for r in envelope.rcpttos)
        to_admin = rcpttos == ('admin@taagekammeret.dk',)
        subject = envelope.message.subject
        subject_str = str(subject)
        delivery_status_subject = (
            'Delayed Mail' in subject_str or
            'Undelivered Mail Returned to Sender' in subject_str)
        if to_admin and delivery_status_subject:
            return 'Subject looks like a DSN'

        # Reject if a header is not encoded properly
        header_items = envelope.message.header_items()
        headers = [header for field, header in header_items]
        chunks = sum((header._chunks for header in headers), [])
        any_unknown = any(charset == email.charset.UNKNOWN8BIT
                          for string, charset in chunks)
        if any_unknown:
            return 'invalid header encoding'

        if envelope.mailfrom == '<>':
            # RFC 5321, 4.5.5. Messages with a Null Reverse-Path:
            # "[Automated email processors] SHOULD NOT reply to messages
            # with a null reverse-path, and they SHOULD NOT add a non-null
            # reverse-path, or change a null reverse-path to a non-null one,
            # to such messages when forwarding."
            # Since we would forward this message with a non-null reverse-path,
            # we should reject it instead.
            return 'null reverse-path'

        n_from = len(envelope.message.get_all_headers('From'))
        if n_from != 1:
            return 'wrong number of From-headers (%s)' % n_from
        if not envelope.from_domain:
            return 'invalid From-header'
        dkim_sigs = envelope.message.get_all_headers('DKIM-Signature')
        if envelope.strict_dmarc_policy and not dkim_sigs:
            return ('%s has strict DMARC policy, ' % envelope.from_domain +
                    'but message has no DKIM-Signature header')

    def handle_envelope(self, envelope, peer):
        if self.handle_delivery_report(envelope):
            return
        envelope.from_domain = self.get_from_domain(envelope)
        envelope.strict_dmarc_policy = self.strict_dmarc_policy(envelope)
        reject_reason = self.reject(envelope)
        if reject_reason:
            summary = 'Rejected by TKForwarder.reject (%s)' % reject_reason
            logger.info('%s', summary)
            self.store_failed_envelope(envelope, summary, summary)
            return
        return super(TKForwarder, self).handle_envelope(envelope, peer)

    def get_from_domain(self, envelope):
        from_domain_mo = re.search(r'@([^ \t\n>]+)',
                                   envelope.message.get_header('From', ''))
        if from_domain_mo:
            return from_domain_mo.group(1)

    def strict_dmarc_policy(self, envelope):
        if envelope.from_domain:
            return has_strict_dmarc_policy(envelope.from_domain)

    def translate_subject(self, envelope):
        subject = envelope.message.subject
        subject_decoded = str(subject)

        if '[TK' in subject_decoded:
            # No change
            return None

        if envelope.strict_dmarc_policy:
            logger.info('Not rewriting subject on email from %r',
                        envelope.message.get_header('From'))
            return None

        try:
            chunks = subject._chunks
        except AttributeError:
            logger.warning('envelope.message.subject does not have _chunks')
            chunks = email.header.decode_header(subject_decoded)

        # No space in '[TK]', since the chunks are joined by spaces.
        subject_chunks = [('[TK]', None)] + list(chunks)
        return email.header.make_header(subject_chunks)

    def translate_recipient(self, rcptto):
        name, domain = rcptto.split('@')
        recipients, origin = tkmail.address.translate_recipient(
            self.year, name, list_ids=True)
        if not recipients:
            logger.info("%s resolved to the empty list", name)
            raise InvalidRecipient(rcptto)
        recipients.sort(key=lambda r: origin[r])
        group_iter = itertools.groupby(recipients, key=lambda r: origin[r])
        groups = [RecipientGroup(origin=o, recipients=frozenset(group))
                  for o, group in group_iter]
        return groups

    def get_group_recipients(self, group):
        return group.recipients

    def get_envelope_mailfrom(self, envelope, recipients=None):
        return self.__class__.MAIL_FROM

    def get_extra_headers(self, envelope, group):
        sender = self.get_envelope_mailfrom(envelope)
        list_name = str(group.origin).lower()
        list_id = '%s.TAAGEKAMMERET.dk' % list_name
        unsub = '<mailto:%s?subject=unsubscribe%%20%s>' % (sender, list_name)
        help = '<mailto:%s?subject=list-help>' % (sender,)
        sub = '<mailto:%s?subject=subscribe%%20%s>' % (sender, list_name)
        headers = [
            ('Sender', sender),
            ('List-Name', list_name),
            ('List-Id', list_id),
            ('List-Unsubscribe', unsub),
            ('List-Help', help),
            ('List-Subscribe', sub),
        ]
        if isinstance(group.origin, GroupAlias):
            headers.extend([
                ('Precedence', 'bulk'),
                ('X-Auto-Response-Suppress', 'OOF'),
            ])
        return headers

    def log_invalid_recipient(self, envelope, exn):
        # Use logging.info instead of the default logging.error
        logger.info('Invalid recipient: %r', exn.args)

    def handle_invalid_recipient(self, envelope, exn):
        self.store_failed_envelope(
            envelope, str(exn), 'Invalid recipient: %s' % exn)

    def handle_error(self, envelope, str_data):
        exc_value = sys.exc_info()[1]
        exc_typename = type(exc_value).__name__
        filename, line, function, text = traceback.extract_tb(
            sys.exc_info()[2])[0]

        tb = ''.join(traceback.format_exc())
        if envelope:
            try:
                self.store_failed_envelope(
                    envelope, str(tb),
                    '%s: %s' % (exc_typename, exc_value))
            except:
                logger.exception("Could not store_failed_envelope")

        exc_key = (filename, line, exc_typename)

        if exc_key not in self.exceptions:
            self.exceptions.add(exc_key)
            self.forward_to_admin(envelope, str_data, tb)

    def forward_to_admin(self, envelope, str_data, tb):
        # admin_emails = tkmail.address.get_admin_emails()
        admin_emails = ['mathiasrav@gmail.com']

        sender = recipient = 'admin@TAAGEKAMMERET.dk'

        if envelope:
            subject = '[TK-mail] Unhandled exception in processing'
            body = textwrap.dedent(self.ERROR_TEMPLATE).format(
                traceback=tb, mailfrom=envelope.mailfrom,
                rcpttos=envelope.rcpttos, message=envelope.message)

        else:
            subject = '[TK-mail] Could not construct envelope'
            body = textwrap.dedent(self.ERROR_TEMPLATE_CONSTRUCTION).format(
                traceback=tb, data=str_data)

        admin_message = Message.compose(
            sender, recipient, subject, body)
        admin_message.add_header('Auto-Submitted', 'auto-replied')

        self.deliver(admin_message, admin_emails, sender)

    def store_failed_envelope(self, envelope, description, summary,
                              inner_envelope=None):
        now = now_string()

        try:
            os.mkdir('error')
        except FileExistsError:
            pass

        with open('error/%s.mail' % now, 'wb') as fp:
            fp.write(envelope.message.as_bytes())

        if inner_envelope is None:
            inner_envelope = envelope
        with open('error/%s.json' % now, 'w') as fp:
            metadata = {
                'mailfrom': envelope.mailfrom,
                'rcpttos': envelope.rcpttos,
                'subject': str(inner_envelope.message.subject),
                'date': inner_envelope.message.get_header('Date'),
                'summary': summary,
            }
            json.dump(metadata, fp)

        with open('error/%s.txt' % now, 'w') as fp:
            fp.write('From %s\n' % inner_envelope.mailfrom)
            fp.write('To %s\n\n' % inner_envelope.rcpttos)
            fp.write('%s\n' % description)

        with open('error/%s.txt' % now, 'a') as fp:
            try:
                g = DecodingDecodedGenerator(fp)
                g.flatten(inner_envelope.message.message)
            except Exception as exn:
                logger.exception(
                    'Could not write message with DecodingDecodedGenerator')
                fp.write('[Exception in generator; see %s.mail]\n' %
                         now)
