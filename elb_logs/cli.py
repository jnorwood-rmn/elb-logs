from __future__ import absolute_import
from calendar import timegm
from collections import OrderedDict
from itertools import izip, izip_longest
import json
import os
import re
import shlex
import click
import boto3
from dateutil import parser
import jmespath


class State(object):
    pass


pass_state = click.make_pass_decorator(State, ensure=True)

@click.group()
@click.option('--profile')
@click.option('--region')
@pass_state
def cli(state, profile, region):
    state.profile = profile
    state.region = region
    boto3.setup_default_session(profile_name=profile, region_name=region)

@cli.command()
@click.option('--bucket', 'bucket_name', required=True)
@click.option('--time-prefix', required=True)
@click.option('--elb', required=True)
@click.option('--output-dir', default=os.getcwd())
@pass_state
def download(state, bucket_name, time_prefix, elb, output_dir):
    account = account_number()

    time_match = re.match(r'(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})(T(?P<time>\d)+)?', time_prefix)
    if not time_match:
        raise click.ClickException('time_prefix argument should be formatted like: 20150121T01...')

    time_path = '{year}/{month}/{day}'.format(**time_match.groupdict())
    s3prefix_format = ('AWSLogs/{account}/elasticloadbalancing/{region}/{time_path}/'
                       '{account}_elasticloadbalancing_{region}_{elb}_{time_prefix}')
    s3prefix = s3prefix_format.format(time_path=time_path,
                                      elb=elb,
                                      time_prefix=time_prefix,
                                      account=account,
                                      region=state.region)
    s3client = boto3.client('s3')
    s3 = boto3.resource('s3')
    bucket = s3.Bucket(bucket_name)
    download_dir = '{output_dir}/{bucket}/{time}/'.format(output_dir=output_dir,
                                                          bucket=bucket_name,
                                                          time=time_prefix)
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    for obj in bucket.objects.filter(Prefix=s3prefix):
        filename = obj.key.split('/')[-1]
        output_file = os.path.join(download_dir, filename)
        click.echo("'s3://{bucket}/{key}' > '{output_file}'".format(bucket=bucket_name,
                                                                    key=obj.key,
                                                                    output_file=output_file))
        s3client.download_file(bucket_name, obj.key, output_file)


@cli.command()
@click.argument('input-files', nargs=-1, type=click.File('rb'))
def parse(input_files):

    def parse_address(addr):
        try:
            ip, port = addr.split(':')
        except Exception:
            return addr
        return {'ip': ip, 'port': int(port)}

    def unparse_address(addr):
        return '{ip}:{port}'.format(**addr)

    fields = OrderedDict((
        ('timestamp', (lambda x: timegm(parser.parse(x).timetuple()), lambda x: x.isoformat())),
        ('elb', (str, str)),
        ('client', (parse_address, unparse_address)),
        ('backend', (parse_address, unparse_address)),
        ('request_processing_time', (float, float)),
        ('backend_processing_time', (float, float)),
        ('response_processing_time', (float, float)),
        ('elb_status_code', (int, int)),
        ('backend_status_code', (int, int)),
        ('received_bytes', (int, int)),
        ('sent_bytes', (int, int)),
        ('request', (str, str)),
    ))

    def parse_line(l, filename):
        parsed = {key: conv(val) for key, (conv, _), val in izip(fields.iterkeys(), fields.itervalues(), shlex.split(l))}
        parsed['_line'] = l
        parsed['_file'] = filename
        return parsed

    for f in input_files:
        for line in f:
            try:
                pl = parse_line(line, f.name)
                click.echo(json.dumps(pl))
            except Exception as e:
                click.echo(e, err=True)


@cli.command('filter')
@click.option('--expression', required=True)
@click.argument('input-files', nargs=-1, type=click.File('rb'))
def input_filter(expression, input_files):

    line_filter = jmespath.compile(expression)

    def grouper(iterable, n, fillvalue=None):
        "Collect data into fixed-length chunks or blocks"
        # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx
        args = [iter(iterable)] * n
        return izip_longest(fillvalue=fillvalue, *args)

    for f in input_files:
        for line_group in grouper(f, 1000, '{}'):
            try:
                filtered = line_filter.search(map(json.loads, line_group))
            except Exception as e:
                click.echo(e, err=True)
            else:
                for fl in filtered:
                    click.echo(json.dumps(fl))


def account_number():
    iam = boto3.resource('iam')
    arn = iam.CurrentUser().arn
    return arn.split(':')[4]

def main():
    return cli(auto_envvar_prefix='ELB_LOGS')

if __name__ == '__main__':
    main()