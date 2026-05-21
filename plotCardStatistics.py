import argparse
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--no-basics',   dest='include_basics',   action='store_false',
                    help='Exclude Basic actions from the analysis')
parser.add_argument('--no-boosters', dest='include_boosters', action='store_false',
                    help='Exclude Booster actions from the analysis')
attack_type = parser.add_mutually_exclusive_group()
attack_type.add_argument('--melee',  action='store_true', help='Show only melee attacks (all range values == 0)')
attack_type.add_argument('--ranged', action='store_true', help='Show only ranged attacks (any range value > 0)')
args = parser.parse_args()

# ── Data loading ──────────────────────────────────────────────────────────────
frames = [pd.read_csv('Weapon actions.csv')]
if args.include_basics:
    frames.append(pd.read_csv('Basic actions.csv'))
if args.include_boosters:
    frames.append(pd.read_csv('Booster actions.csv'))

df = pd.concat(frames, ignore_index=True)

def parse_numeric_field(val):
    if pd.isna(val):
        return np.nan
    s = str(val).strip().lstrip('+')
    if not s:
        return np.nan
    return float(s.split(',')[0])

df['Initiative_parsed'] = df['Initiative'].apply(parse_numeric_field)
df['Movement_parsed']   = df['Movement'].apply(parse_numeric_field)
df['TotalAttacks'] = df['HighAttack'] + df['MidAttack'] + df['LowAttack']
df['TotalBlocks']  = df['HighBlock']  + df['MidBlock']  + df['LowBlock']

is_ranged = (df[['HighRange', 'MidRange', 'LowRange']].fillna(0) > 0).any(axis=1)
if args.ranged:
    df = df[is_ranged].reset_index(drop=True)
elif args.melee:
    df = df[~is_ranged].reset_index(drop=True)

attack_suffix = ' — Ranged only' if args.ranged else (' — Melee only' if args.melee else '')

# ── Color palette by Group ────────────────────────────────────────────────────
groups = sorted(df['Group'].dropna().unique())
try:
    _cmap = plt.colormaps['tab20']
except AttributeError:
    _cmap = plt.cm.get_cmap('tab20')
color_map = {g: _cmap(i / 20) for i, g in enumerate(groups)}

# ── Style ─────────────────────────────────────────────────────────────────────
for style in ('seaborn-v0_8-darkgrid', 'seaborn-darkgrid', 'ggplot'):
    try:
        plt.style.use(style)
        break
    except OSError:
        continue

# ── Scatter helper ────────────────────────────────────────────────────────────
_BASE_SCATTER_SIZE = 20

def _scatter_stacked(ax, data, xcol, ycol, base_size=_BASE_SCATTER_SIZE):
    """Scatter plot where cards sharing a location get progressively larger rings."""
    valid = data.dropna(subset=[xcol, ycol]).copy()
    loc_rank = {}
    ranks = []
    for _, row in valid.iterrows():
        key = (row[xcol], row[ycol])
        loc_rank[key] = loc_rank.get(key, 0) + 1
        ranks.append(loc_rank[key])
    valid['_rank'] = ranks
    valid['_size'] = valid['_rank'] * base_size
    valid['_color'] = valid['Group'].map(color_map)
    # Draw largest first so smaller circles appear on top
    valid = valid.sort_values('_size', ascending=False)
    ax.scatter(valid[xcol], valid[ycol],
               c=valid['_color'].tolist(), s=valid['_size'],
               alpha=0.85, edgecolors='white', linewidths=0.5)
    for _, row in valid.iterrows():
        ax.annotate(row['Name'], (row[xcol], row[ycol]),
                    fontsize=5.5, alpha=0.65,
                    textcoords='offset points', xytext=(3, 2))

# ── Figure 1: Trade-off scatter plots ─────────────────────────────────────────
fig1, axes = plt.subplots(2, 2, figsize=(15, 11))
fig1.suptitle(f'Card Balance Trade-offs{attack_suffix}', fontsize=16, fontweight='bold')

scatter_configs = [
    (axes[0, 0], 'Initiative_parsed', 'Movement_parsed', 'Initiative vs Movement'),
    (axes[0, 1], 'Initiative_parsed', 'TotalAttacks',    'Initiative vs Total Attacks'),
    (axes[1, 0], 'Movement_parsed',   'TotalAttacks',    'Movement vs Total Attacks'),
    (axes[1, 1], 'Initiative_parsed', 'TotalBlocks',     'Initiative vs Total Blocks'),
]

for ax, xcol, ycol, title in scatter_configs:
    _scatter_stacked(ax, df, xcol, ycol)
    ax.set_xlabel(xcol.replace('_parsed', ''))
    ax.set_ylabel(ycol.replace('_parsed', ''))
    ax.set_title(title, fontsize=11)

# Quadrant annotation on Initiative vs Blocks — high init + blocks = strong defensive
ax4 = axes[1, 1]
med_init   = df['Initiative_parsed'].median()
med_blocks = df['TotalBlocks'].median()
ax4.axvline(med_init,   color='gray', linestyle='--', alpha=0.4, linewidth=1)
ax4.axhline(med_blocks, color='gray', linestyle='--', alpha=0.4, linewidth=1)
ax4.text(0.97, 0.97, 'Strong defensive\n(acts first, keeps blocks)',
         ha='right', va='top', transform=ax4.transAxes,
         fontsize=7, color='seagreen', alpha=0.9,
         bbox=dict(boxstyle='round', facecolor='white', alpha=0.6))
ax4.text(0.03, 0.97, 'Block likely consumed\nbefore card resolves',
         ha='left', va='top', transform=ax4.transAxes,
         fontsize=7, color='firebrick', alpha=0.9,
         bbox=dict(boxstyle='round', facecolor='white', alpha=0.6))

handles = [plt.Line2D([0], [0], marker='o', color='w',
                       markerfacecolor=color_map[g], markersize=8, label=g)
           for g in groups]
fig1.legend(handles=handles, title='Group', bbox_to_anchor=(1.0, 0.5),
            loc='center left', fontsize=8)
fig1.tight_layout(rect=[0, 0, 0.88, 1])

# ── Figure 2: Attack zone distribution by group ───────────────────────────────
group_atk = df.groupby('Group')[['HighAttack', 'MidAttack', 'LowAttack']].mean()
group_atk['Total'] = group_atk.sum(axis=1)
group_atk = group_atk.sort_values('Total', ascending=True)

fig2, ax2 = plt.subplots(figsize=(10, 7))
fig2.suptitle(f'Average Attack Dice by Zone per Group{attack_suffix}', fontsize=14, fontweight='bold')

y = np.arange(len(group_atk))
zone_cols   = ['HighAttack', 'MidAttack', 'LowAttack']
zone_labels = ['High', 'Mid', 'Low']
zone_colors = ['#e74c3c', '#f39c12', '#3498db']

lefts = np.zeros(len(group_atk))
for col, label, color in zip(zone_cols, zone_labels, zone_colors):
    vals = group_atk[col].values
    ax2.barh(y, vals, left=lefts, height=0.65, color=color, label=label, edgecolor='white')
    for i, (v, l) in enumerate(zip(vals, lefts)):
        if v >= 0.25:
            ax2.text(l + v / 2, i, f'{v:.1f}', ha='center', va='center',
                     fontsize=8, color='white', fontweight='bold')
    lefts += vals

# Total label at end of each bar
for i, total in enumerate(group_atk['Total'].values):
    ax2.text(total + 0.05, i, f'{total:.1f}', va='center', fontsize=8, color='dimgray')

ax2.set_yticks(y)
ax2.set_yticklabels(group_atk.index, fontsize=10)
ax2.set_xlabel('Average Attacks per Card')
ax2.legend(title='Zone', loc='lower right')
fig2.tight_layout()

# ── Figure 3: Group meta-statistics grouped bar chart ─────────────────────────
stats_cols  = ['Initiative_parsed', 'Movement_parsed', 'TotalAttacks', 'TotalBlocks']
stat_labels = ['Initiative', 'Movement', 'Total Attacks', 'Total Blocks']
stat_colors = ['#9b59b6', '#1abc9c', '#e74c3c', '#3498db']

group_means   = df.groupby('Group')[stats_cols].mean()
group_stds    = df.groupby('Group')[stats_cols].std().fillna(0)
overall_means = df[stats_cols].mean()

n_groups = len(group_means)
n_stats  = len(stats_cols)
x        = np.arange(n_groups)
width    = 0.18
offsets  = np.linspace(-(n_stats - 1) / 2 * width, (n_stats - 1) / 2 * width, n_stats)

fig3, ax3 = plt.subplots(figsize=(16, 7))
fig3.suptitle(f'Group Meta-Statistics (mean ± std){attack_suffix}', fontsize=14, fontweight='bold')

for i, (col, label, color) in enumerate(zip(stats_cols, stat_labels, stat_colors)):
    means = group_means[col].values
    stds  = group_stds[col].values
    ax3.bar(x + offsets[i], means, width, yerr=stds, label=label,
            color=color, alpha=0.8, capsize=3, error_kw={'linewidth': 1})
    ax3.axhline(overall_means[col], color=color, linestyle='--', alpha=0.45, linewidth=1.2)

ax3.set_xticks(x)
ax3.set_xticklabels(group_means.index, rotation=35, ha='right', fontsize=9)
ax3.set_ylabel('Value')
ax3.legend(title='Stat', loc='upper right')
fig3.tight_layout()

# ── Figure 4: Correlation heatmap + power budget radar ────────────────────────
fig4 = plt.figure(figsize=(16, 7))
fig4.suptitle(f'Aggregate Analysis{attack_suffix}', fontsize=14, fontweight='bold')
gs4 = gridspec.GridSpec(1, 2, width_ratios=[1, 1.5])

# Left: Pearson correlation heatmap
ax_heat = fig4.add_subplot(gs4[0])
corr = df[stats_cols].corr()
im = ax_heat.imshow(corr.values, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
ax_heat.set_xticks(range(n_stats))
ax_heat.set_yticks(range(n_stats))
ax_heat.set_xticklabels(stat_labels, rotation=30, ha='right', fontsize=9)
ax_heat.set_yticklabels(stat_labels, fontsize=9)
for i in range(n_stats):
    for j in range(n_stats):
        ax_heat.text(j, i, f'{corr.iloc[i, j]:.2f}',
                     ha='center', va='center', fontsize=11,
                     color='black', fontweight='bold')
plt.colorbar(im, ax=ax_heat, shrink=0.8)
ax_heat.set_title('Stat Correlation Matrix', fontsize=11)

# Right: radar chart — each group's normalized power budget
ax_radar = fig4.add_subplot(gs4[1], polar=True)

N      = n_stats
angles = [n / float(N) * 2 * np.pi for n in range(N)]
angles += angles[:1]

radar_data = group_means.copy()
for col in stats_cols:
    mn = radar_data[col].min()
    mx = radar_data[col].max()
    radar_data[col] = (radar_data[col] - mn) / (mx - mn) if mx > mn else 0.5

# Overall mean reference polygon
overall_radar = radar_data.mean().values.tolist()
overall_radar += overall_radar[:1]
ax_radar.plot(angles, overall_radar, 'k--', linewidth=2, alpha=0.5, label='Overall mean')
ax_radar.fill(angles, overall_radar, color='black', alpha=0.06)

for group in radar_data.index:
    vals = radar_data.loc[group, stats_cols].values.tolist()
    vals += vals[:1]
    color = color_map.get(group, 'gray')
    ax_radar.plot(angles, vals, color=color, linewidth=1.5, label=group)
    ax_radar.fill(angles, vals, color=color, alpha=0.04)

ax_radar.set_xticks(angles[:-1])
ax_radar.set_xticklabels(stat_labels, fontsize=9)
ax_radar.set_ylim(0, 1)
ax_radar.set_yticks([0.25, 0.5, 0.75])
ax_radar.set_yticklabels(['25%', '50%', '75%'], fontsize=7)
ax_radar.set_title('Power Budget by Group\n(normalized per stat)', fontsize=11, pad=20)
ax_radar.legend(loc='upper right', bbox_to_anchor=(1.55, 1.1),
                fontsize=7, title='Group')

fig4.tight_layout()

# ── Figure 5: Attack cost (0.4×Initiative + 0.6×Movement) vs Total Attacks ───
df['AttackCost'] = 0.4 * df['Initiative_parsed'] + 0.6 * df['Movement_parsed']

fig5, ax5 = plt.subplots(figsize=(10, 7))
fig5.suptitle(f'Attack Cost vs Total Attacks{attack_suffix}', fontsize=14, fontweight='bold')

cost_atk = df.dropna(subset=['AttackCost', 'TotalAttacks'])
_scatter_stacked(ax5, cost_atk, 'AttackCost', 'TotalAttacks')

med_cost = cost_atk['AttackCost'].median()
med_atk  = cost_atk['TotalAttacks'].median()
ax5.axvline(med_cost, color='gray', linestyle='--', alpha=0.4, linewidth=1)
ax5.axhline(med_atk,  color='gray', linestyle='--', alpha=0.4, linewidth=1)
ax5.text(0.97, 0.97, 'High cost,\nhigh attacks',
         ha='right', va='top', transform=ax5.transAxes,
         fontsize=7, color='firebrick', alpha=0.9,
         bbox=dict(boxstyle='round', facecolor='white', alpha=0.6))
ax5.text(0.03, 0.03, 'Low cost,\nfew attacks',
         ha='left', va='bottom', transform=ax5.transAxes,
         fontsize=7, color='seagreen', alpha=0.9,
         bbox=dict(boxstyle='round', facecolor='white', alpha=0.6))

ax5.set_xlabel('Attack Cost  (0.4 × Initiative + 0.6 × Movement)')
ax5.set_ylabel('Total Attacks')

handles5 = [plt.Line2D([0], [0], marker='o', color='w',
                        markerfacecolor=color_map[g], markersize=8, label=g)
            for g in groups]
ax5.legend(handles=handles5, title='Group', bbox_to_anchor=(1.01, 1),
           loc='upper left', fontsize=8)
fig5.tight_layout()

plt.show()
