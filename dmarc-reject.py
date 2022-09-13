import os
import email

from dvmail.delivery_reports import email_delivery_reports


counter = {'Apple<appleid@id.apple.com>': 0,
           '"Instagram" <no-reply@mail.instagram.com>': 0,
           'Google <no-reply@accounts.google.com>': 0,
           'Nykredit <komm@mail.nykredit.dk>': 0,
           'Nykredit <kundeservice@erhverv.nykredit.dk>': 0,
           '<*@facebookmail.com>': 0,
           'Apple<appleid_dkda@email.apple.com>': 0,
           '<*@linkedin.com>': 0}


real_emails = 0
first = True

for base, report in email_delivery_reports():
    if first:
        print("First report is %s\n" % base)
        first = False
    if 'DMARC' in report.notification:
        undelivered_message = report.message
        from_ = undelivered_message['From']
        if from_.endswith('@facebookmail.com>'):
            counter['<*@facebookmail.com>'] += 1
            continue
        elif from_.endswith('@linkedin.com>'):
            counter['<*@linkedin.com>'] += 1
            continue
        else:
            try:
                counter[from_] += 1
            except KeyError:
                pass
            else:
                continue
        real_emails += 1
        print(real_emails, base)
        for k, v in undelivered_message.items():
            if k.lower() in ('return-path', 'received') or k.startswith('List-'):
                continue
            if k.lower() in ('from', 'date', 'subject') or 'DATAVENSKAB' in str(v).upper():
                h = email.header.make_header(email.header.decode_header(v))
                print('%s: %s' % (k, h))
        print('', flush=True)
print(counter)
