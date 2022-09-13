def get_extra_headers(sender, list_name, is_group, skip=()):
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
    if is_group:
        headers.extend([
            ('Precedence', 'bulk'),
            ('X-Auto-Response-Suppress', 'OOF'),
        ])
    headers = [(k, v) for k, v in headers if k.lower() not in skip]
    return headers
