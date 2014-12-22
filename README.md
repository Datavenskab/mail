## A lightweight email forwarding framework

### Introduction

`emailtunnel` is a small Python 3 framework that uses the `smtpd`, `smtplib`
and `email` modules in the Python standard library to implement simple mailing
list forwarding.

The user must supply a function that maps symbolic recipient addresses on their
own domain to user email addresses.

A simple example, operating on the domain `maillist.local` with two users
and three lists:

```
USERS = {
    'admin@maillist.local': ['c2h5oh@example.com'],
    'luser@maillist.local': ['noreply@yahoo.com'],
    'all@maillist.local': ['c2h5oh@example.com', 'noreply@yahoo.com'],
}

class SimpleForwarder(emailtunnel.SMTPForwarder):
    def translate_recipient(self, rcptto):
        try:
            return USERS[rcptto]
        except KeyError:
            raise emailtunnel.InvalidRecipient(rcptto)

    def translate_subject(self, envelope):
        return '[Simple-List] %s' % envelope.message.subject
```

The `translate_recipient` method either returns a list of external
recipients to relay the envelope on to these,
or it may return the empty list to silently drop the email,
or it may raise `InvalidRecipient` to respond with an SMTP error.


### Repository overview

The framework is implemented in `emailtunnel/__init__.py`,
implementing the following classes:

* InvalidRecipient (exception)
* Message (encapsulating an instance of `email.message.Message`)
* Envelope (encapsulating a Message, a recipient and a sender)
* SMTPReceiver (abstract subclass of `smtpd.SMTPServer`)
* LoggingReceiver (simple implementation of SMTPReceiver)
* RelayMixin (mixin providing envelope delivery to a relay)
* SMTPForwarder (subclass of SMTPReceiver)

The framework may be tested by running `python -m emailtunnel --help`,
which runs the code in `emailtunnel/__main__.py`
that allows simple logging and relaying of emails.

The `emailtunnel.send` module may be run from the command line to send simple
emails specified via command line parameters and standard input.


### Application example

The `tkmail.server` module implements `TKForwarder`, an application of
`emailtunnel.SMTPForwarder`.

It supports logging of exceptions and misdeliveries to a list of admins,
and it uses the `tkmail.address` module to perform delicate parsing of
recipient addresses.

The `TKForwarder` is started by running the `tkmail` module from the command
line by calling `python -m tkmail --help`.

The `tkmail.monitor` module is designed to be run daily from a cron job,
and it checks the error directory and sends a report to admins.

The `tkmail.test` module starts an instance of `TKForwarder`,
feeds it test messages and checks the relayed messages for correctness.


### SMTPForwarder logic

The main entry point from the `smtpd` module is `SMTPReceiver.process_message`.
First, the message data is stored in an instance of `Message`,
which performs a sanity roundtrip parsing check
to make sure that `str(Message(data)) == str(Message(str(Message(data))))`
modulo trailing whitespace.

Then, the envelope is passed to `handle_envelope`,
which is implemented in a subclass (such as SMTPForwarder).
If `handle_envelope` returns None, `smtpd` assumes that the envelope was
successfully delivered.
Otherwise, it must return a string, which is returned to the SMTP remote peer.
If an exception occurs in `handle_envelope`, SMTP error 451 is returned to the
peer ("Requested action aborted: error in processing").
The subclass may implement `handle_error` to do further logging.

The `SMTPForwarder` class implements `handle_envelope`
by transforming the Subject via `translate_subject` in the subclass
and by transforming the list of recipients via `translate_recipients`.
The default implementation of `translate_recipients` transforms each recipient
using `translate_recipient`, which is the identity map by default.

The envelope is passed on with only the subject changed
using `RelayMixin.deliver`, which requires the attributes
`relay_host` and `relay_port` to be set.

If `InvalidRecipient` is raised during `translate_recipients`, SMTP error 554
is returned to the SMTP peer (Transaction failed: mailbox unavailable)
and no email is relayed. We must return 554 instead of the traditional 550,
since we respond to SMTP DATA, and not to SMTP RCPT.