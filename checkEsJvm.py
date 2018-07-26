#!/usr/bin/env python
"""Check elasticsearch JVM
This is a work in progress"""

from nagioscheck import NagiosCheck, UsageError
from nagioscheck import PerformanceMetric, Status
import urllib2

try:
    import json
except ImportError:
    import simplejson as json

class ElasticSearchJvmCheck(NagiosCheck):
    version = '0.3.0'

    def __init__(self):
        NagiosCheck.__init__(self)

        self.add_option('H', 'host', 'host', "Hostname or network "
                        "address to probe.  The ElasticSearch API "
                        "should be listening here.  Defaults to "
                        "'localhost'.")

        self.add_option('p', 'port', 'port', "TCP port to probe.  "
                        "The ElasticSearch API should be listening "
                        "here.  Defaults to 9200.")

        self.add_option('w', 'warn', 'warn', "Warning threshold percent")
        self.add_option('c', 'crit', 'crit', "Critical threshold percent")

    def check(self, opts, args):
        host = opts.host or "localhost"
        port = int(opts.port or '9200')

        #
        # Data retrieval
        #

        # Request a bunch of useful numbers that we export as perfdata.
        # Details like the number of get, search, and indexing operations come from here.
        es_node = get_json(r'http://%s:%d/_nodes/_local/?all=true' % (host, port))

        es_stats = get_json(r'http://%s:%d/_nodes/_local/'
                             'stats?all=true' % (host, port))

        myid = es_stats['nodes'].keys()[0]
    
        es_node_jvm = get_json(r'http://%s:%d/_nodes/%s/stats?jvm=true' % (host, port, myid))

        #
        # Perfdata
        #

        perfdata = []

        def dict2perfdata(base, metrics):
            for metric in metrics:
                if len(metric) == 2:
                    label, path = metric
                    unit = ""
                elif len(metric) > 2:
                    label, path, unit = metric
                else:
                    continue

                keys = path.split(".")

                value = base
                for key in keys:
                    if value is None:
                        break
                    try:
                        value = value[key]
                    except KeyError:
                        value = None
                        break

                if value is not None:
                    metric = PerformanceMetric(label=label,
                                               value=value,
                                               unit=unit)
                    perfdata.append(metric)

        # Add cluster-wide metrics first.
        #
        metrics = [["heap_committed",         'heap_committed_in_bytes',                "B"],
                   ["heap_used",              'heap_used_in_bytes',                     "B"],
                   ["non_heap_committed",     'non_heap_committed_in_bytes',            "B"],
                   ["non_heap_used",          'non_heap_used_in_bytes',                 "B"],
                   ["old_gen_used",           'pools.CMS Old Gen.used_in_bytes',        "B"],
                   ["perm_gen_used",          'pools.CMS Perm Gen.used_in_bytes',       "B"],
                   ["eden_used",              'pools.Par Eden Space.used_in_bytes',     "B"],
                   ["survivor_used",          'pools.Par Survivor Space.used_in_bytes', "B"],
                   ["code_cache",             'pools.Code Cache.used_in_bytes',         "B"]
        ]

        dict2perfdata(es_stats['nodes'][myid]['jvm']['mem'], metrics)

        metrics = [["heap_max",               'heap_max_in_bytes',                      "B"],
                   ["non_heap_max",           'non_heap_max_in_bytes',                  "B"],
                   ["direct_max",             'direct_max_in_bytes',                    "B"]]

        dict2perfdata(es_node['nodes'][myid]['jvm']['mem'], metrics)

        collectors = es_node_jvm["nodes"][myid]["jvm"]["gc"]["collectors"].keys()
        for collector in collectors:
            # Full collector names are too long for RRD; contract them
            # to initialisms. 
            collector_initials = "".join([ c for c in collector if (
                ord(c) >= ord('A') and
                ord(c) <= ord('Z')
            )])
            collector_slug = collector_initials.lower()
            metrics = [["%s_collections" % collector_slug, "collectors.%s.collection_count" % collector, "c"],
                       ["%s_time_ms" % collector_slug, "collectors.%s.time_in_millis" % collector, "c"],
            ]
            dict2perfdata(es_node_jvm["nodes"][myid]["jvm"]["gc"], metrics)

        metrics = [["collections", "collection_count", "c"],
                   ["collection_time_ms", "collection_time_in_millis", "c"],
        ]
        dict2perfdata(es_node_jvm["nodes"][myid]["jvm"]["gc"], metrics)

        heap_used_b = int(es_stats['nodes'][myid]['jvm']['mem']['heap_used_in_bytes'])
        heap_max_b  = int(es_node ['nodes'][myid]['jvm']['mem']['heap_max_in_bytes'])
        # ES 1.0 changed their keys, dropping heap_used & heap_max.  If they don't exist, lets create them again from
        # their byte values.  Expected to be of the form '1.3gb'
        try:
            heap_used   = es_stats['nodes'][myid]['jvm']['mem']['heap_used']
            heap_max    = es_node ['nodes'][myid]['jvm']['mem']['heap_max']
        except KeyError:
            heap_used   = str(round(es_stats['nodes'][myid]['jvm']['mem']['heap_used_in_bytes']/1048576.0,2))+'gb'
            heap_max    = str(round(es_node ['nodes'][myid]['jvm']['mem']['heap_max_in_bytes']/1048576.0,2))+'gb'

        heap_usage_percent = (float(heap_used_b)/float(heap_max_b))*100

        if opts.crit and heap_usage_percent >= float(opts.crit):
            raise Status("critical","JVM Heap Usage: %d%% %s/%s" % (heap_usage_percent,str(heap_used),str(heap_max)),perfdata)
        if opts.warn and heap_usage_percent >= float(opts.warn):
            raise Status("warning","JVM Heap Usage: %d%% %s/%s" % (heap_usage_percent,str(heap_used),str(heap_max)),perfdata)
        raise Status("ok","JVM Heap Usage: %d%% %s/%s" % (heap_usage_percent,str(heap_used),str(heap_max)),perfdata)

def get_json(uri):
    try:
        f = urllib2.urlopen(uri)
    except urllib2.HTTPError, e:
        raise Status('unknown', ("API failure",
                                 None,
                                 "API failure:\n\n%s" % str(e)))
    except urllib2.URLError, e:
        # The server could be down; make this CRITICAL.
        raise Status('critical', (e.reason,))

    body = f.read()

    try:
        j = json.loads(body)
    except ValueError:
        raise Status('unknown', ("API returned nonsense",))

    return j

if __name__ == '__main__':
    ElasticSearchJvmCheck().run()

# test in COE environment first