try:
    from dmarc_policy_parser import get_dmarc_policy
except ImportError:
    print(
        'Couldn\'t import dmarc_policy_parser.  Consider installing\n' +
        'https://github.com/Mortal/dmarc_policy_parser/archive/master.zip\n' +
        'with pip.')

    def get_dmarc_policy(domain, *args, **kwargs):
        return 'none'


def has_strict_dmarc_policy(domain):
    return get_dmarc_policy(domain) in ('reject', 'quarantine')
