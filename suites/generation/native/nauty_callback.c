/* Benchmark adapter for the public OUTPROC hooks of unmodified nauty 2.9.3.
 * The finite benchmark has n <= 14, hence Aut(G) <= 14! < 2^53: nauty's
 * double mantissa represents every possible integral group order exactly.
 * Wire format matches multig -G: n npairs node_aut (u v multiplicity)*. */
#include "nauty.h"
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>

static uint64_t exact_group_order(const statsblk *stats, int n)
{
    uint64_t bound = 1;
    for (int v = 2; v <= n; ++v) bound *= v;
    if (n < 1 || n > 14 || stats->errstatus != 0 || stats->grpsize2 != 0 ||
        !isfinite(stats->grpsize1) || stats->grpsize1 < 1 ||
        stats->grpsize1 > (double)bound ||
        (double)(uint64_t)stats->grpsize1 != stats->grpsize1) {
        fputs("materialized nauty: invalid or inexact group order\n", stderr);
        exit(2);
    }
    return (uint64_t)stats->grpsize1;
}

static void write_graph(FILE *out, graph *g, int n, const statsblk *stats)
{
    int edges = 0;
    for (int u = 0; u < n; ++u)
        for (int v = u + 1; v < n; ++v)
            edges += ISELEMENT(GRAPHROW(g, u, 1), v) != 0;
    fprintf(out, "%d %d %" PRIu64, n, edges, exact_group_order(stats, n));
    for (int u = 0; u < n; ++u)
        for (int v = u + 1; v < n; ++v)
            if (ISELEMENT(GRAPHROW(g, u, 1), v))
                fprintf(out, " %d %d 1", u, v);
    fputc('\n', out);
    if (ferror(out)) {
        fputs("materialized nauty: output failure\n", stderr);
        exit(2);
    }
}

#ifdef MATERIALIZED_GENG
extern TLS_ATTR statsblk nauty_stats;
extern boolean canonise;
void materialized_graph(FILE *out, graph *g, int n)
{
    /* geng documents that -l populates nauty_stats for this output graph. */
    if (!canonise || n < 1 || n > 14) {
        fputs("materialized geng requires -l and n <= 14\n", stderr);
        exit(2);
    }
    write_graph(out, g, n, &nauty_stats);
}
#else
void materialized_tree(FILE *out, int *parent, int n)
{
    graph g[14];
    int lab[14], partition[14], orbits[14];
    static DEFAULTOPTIONS_GRAPH(options);
    statsblk stats;
    if (n < 1 || n > 14) {
        fputs("materialized gentreeg requires n <= 14\n", stderr);
        exit(2);
    }
    EMPTYGRAPH(g, 1, n);
    for (int v = 2; v <= n; ++v) {
        if (parent[v] < 1 || parent[v] >= v) {
            fputs("materialized gentreeg: invalid parent array\n", stderr);
            exit(2);
        }
        ADDONEEDGE(g, v - 1, parent[v] - 1, 1);
    }
    /* Tree generation does not supply the unrooted automorphism order. */
    densenauty(g, lab, partition, orbits, &options, &stats, 1, n, NULL);
    write_graph(out, g, n, &stats);
}
#endif
