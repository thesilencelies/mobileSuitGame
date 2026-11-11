"""helpcard.py

Generates rules reference helpcards for mobileSuitGame:
- build/card/Helpcard_Symbols.tex: Common symbols, combat stats, attack marks & damage types, status effects.
- build/card/Helpcard_Keywords.tex: Comprehensive keywords glossary with simplified summaries.

Together, these form a double-sided rules reference helpcard:
Front: Common Symbols & Combat
Back: Keywords Glossary

Standard card format: 6.9cm x 9.4cm with 5mm bleed (5.9cm x 8.4cm safe area).
"""

from pathlib import Path


def build_symbols_card() -> str:
    """Generate LaTeX TikZ code for the Common Symbols reference card."""
    return r"""\providecommand{\kw}[1]{{\normalfont\bfseries #1}}
\begin{tikzpicture}
  \node (cardbleed) [rectangle, minimum width = 6.9cm, minimum height = 9.4cm, fill=black] at (3.45,4.7){};
  \useasboundingbox (0,0) rectangle (6.9, 9.4);
  \clip (0,0) rectangle (6.9, 9.4);
  \node (cardbg) [rectangle, minimum width = 5.9cm, minimum height = 8.4cm, fill=black!6!white] at (3.45,4.7){};

  % White card plate
  \node (innerplate) [rectangle, rounded corners=3pt, minimum width=5.7cm, minimum height=8.2cm, fill=white, draw=black!40] at (3.45,4.7) {};

  % Content box
  \node[anchor=north, inner sep=0pt, text width=5.4cm] at (3.45, 8.68) {%
    {\centering\textbf{\cardtitlefont\normalsize COMMON SYMBOLS}\par\vspace{1pt}}
    {\color{citysteel}\hrule height 0.8pt}\vspace{2.5pt}

    % --- STATS ---
    {\color{citysteel}\textbf{\cardtitlefont\scriptsize CARD STATS \& COMBAT}}\par\vspace{1.5pt}
    {\fontsize{4.8pt}{5.8pt}\selectfont\cardtextfont
    \begin{tabular}{@{}c@{\hspace{3pt}}l@{\hspace{4pt}}p{3.85cm}@{}}
      \includegraphics[height=0.22cm]{../icons/initImg.png} & \kw{Initiative} & Resolution order. \\
      \includegraphics[height=0.22cm]{../icons/mvimg_old.png} & \kw{Movement} & Speed modifier. Climb: +1/elevation; descend: free. \\
      \includegraphics[height=0.22cm]{../icons/rangeImg.png} & \kw{Range} & Max reach for ranged attack. \\
      \resizebox{0.24cm}{!}{\begin{tikzpicture}\blockshield{0}{0}{cityblue}\end{tikzpicture}} & \kw{Block} & Compulsory if able. 1 block stops all zones; card discarded. \\
      \resizebox{0.29cm}{!}{\begin{tikzpicture}\superblockshield{0}{0}{cityblue}\end{tikzpicture}} & \kw{Super Blk} & As block, but card stays in play revealed. \\
      \tikz{\node[circle, fill=red, inner sep=0.2pt]{\tiny\textbf{\color{white}N}};\strut} & \kw{Persistence} & Card stays active in play for N turns after resolving. \\
    \end{tabular}\par
    }\vspace{2.5pt}
    {\color{citysteel}\hrule height 0.5pt}\vspace{2pt}

    % --- DAMAGE TYPES ---
    {\color{citysteel}\textbf{\cardtitlefont\scriptsize ATTACK MARKS \& DAMAGE TYPES}}\par\vspace{1.5pt}
    {\fontsize{4.8pt}{5.8pt}\selectfont\cardtextfont
    Each mark deals 1 damage to its zone if unblocked.
    Melee attacks shift $\pm$1 zone per elevation difference.\par\vspace{2.5pt}
    {\centering
      \smallcut\ \kw{Cut} \hspace{10pt}
      \smallpierce\ \kw{Pierce} \hspace{10pt}
      \smallimpact\ \kw{Impact}\par\vspace{1.5pt}
      \smallprojectile\ \kw{Projectile} \hspace{12pt}
      \smallenergy\ \kw{Energy}\par
    }
    }\vspace{2.5pt}
    {\color{citysteel}\hrule height 0.5pt}\vspace{2pt}

    % --- STATUS EFFECTS ---
    {\color{citysteel}\textbf{\cardtitlefont\scriptsize STATUS EFFECTS}}\par\vspace{1.5pt}
    {\fontsize{4.8pt}{5.8pt}\selectfont\cardtextfont
    Opposite counters cancel 1-for-1. In Planning, remove 1 of each counter.\par\vspace{2.5pt}
    {\centering
    \begin{tabular}{@{}c@{\hspace{2pt}}l@{\hspace{4pt}}r@{\hspace{14pt}}c@{\hspace{2pt}}l@{\hspace{4pt}}r@{}}
      \includegraphics[height=0.20cm]{../icons/stunned.png} & \kw{Stunned} & $-$2 Init &
      \includegraphics[height=0.20cm]{../icons/stimmed.png} & \kw{Stimmed} & +2 Init \\[1.2pt]
      \includegraphics[height=0.20cm]{../icons/slowed.png} & \kw{Slowed} & $-$2 Move &
      \includegraphics[height=0.20cm]{../icons/boosted.png} & \kw{Boosted} & +2 Move \\[1.2pt]
      \includegraphics[height=0.20cm]{../icons/dazed.png} & \kw{Dazed} & $-$2 Cards &
      \includegraphics[height=0.20cm]{../icons/lucid.png} & \kw{Lucid} & +2 Cards \\[1.2pt]
    \end{tabular}\par\vspace{2.5pt}
    \includegraphics[height=0.20cm]{../icons/revealed.png}\ \kw{Revealed:} Action for the turn is chosen face-up.\par
    }
    }
  };

  \fill[black, even odd rule] (cardbleed.south west) rectangle (cardbleed.north east) (cardbg.south west) rectangle (cardbg.north east);
\end{tikzpicture}
"""


def build_keywords_card() -> str:
    """Generate LaTeX TikZ code for the Keywords Glossary reference card."""
    return r"""\providecommand{\kw}[1]{{\normalfont\bfseries #1}}
\begin{tikzpicture}
  \node (cardbleed) [rectangle, minimum width = 6.9cm, minimum height = 9.4cm, fill=black] at (3.45,4.7){};
  \useasboundingbox (0,0) rectangle (6.9, 9.4);
  \clip (0,0) rectangle (6.9, 9.4);
  \node (cardbg) [rectangle, minimum width = 5.9cm, minimum height = 8.4cm, fill=black!6!white] at (3.45,4.7){};

  % White card plate
  \node (innerplate) [rectangle, rounded corners=3pt, minimum width=5.7cm, minimum height=8.2cm, fill=white, draw=black!40] at (3.45,4.7) {};

  % Content box
  \node[anchor=north, inner sep=0pt, text width=5.4cm] at (3.45, 8.68) {%
    {\centering\textbf{\cardtitlefont\normalsize KEYWORDS GLOSSARY}\par\vspace{1pt}}
    {\color{citysteel}\hrule height 0.8pt}\vspace{2pt}

    % --- ATTACK KEYWORDS ---
    {\color{citysteel}\textbf{\cardtitlefont\scriptsize ATTACK KEYWORDS}}\par\vspace{1pt}
    {\fontsize{4.8pt}{5.8pt}\selectfont\cardtextfont
    \kw{On Hit:}\ The text after the colon applies only if the attack hits (is not blocked).\par\vspace{1.2pt}
    \kw{On Block:}\ The text after the colon applies only if this card is used to block an attack.\par\vspace{1.2pt}
    \kw{Guard Break:}\ This attack consumes one block per zone attacked. Unblocked zones still deal damage.\par\vspace{1.2pt}
    \kw{Committed:}\ This attack is discarded after it resolves (cannot stay out to block later attacks).\par\vspace{1.2pt}
    \kw{Feint:}\ If this attack is not blocked it deals no damage. (Opponents must still block if able).\par\vspace{1.2pt}
    \kw{Close Quarters:}\ This attack cannot be blocked by attacks that have already resolved.\par\vspace{1.2pt}
    \kw{Knockback (X):}\ Move target frame X steps directly away from the source (cannot move up elevation).\par\vspace{1.2pt}
    \kw{Reload:}\ Persists until this weapon next attacks; that attack deals no damage and has no effect, then both discard.\par\vspace{1.2pt}
    \kw{Drone:}\ An independent token unit that acts each turn with this card's attack, using printed movement and health.\par
    }\vspace{2pt}
    {\color{citysteel}\hrule height 0.5pt}\vspace{1.5pt}

    % --- FRAME KEYWORDS ---
    {\color{citysteel}\textbf{\cardtitlefont\scriptsize FRAME KEYWORDS}}\par\vspace{1pt}
    {\fontsize{4.8pt}{5.8pt}\selectfont\cardtextfont
    \kw{Flying:}\ Spends no extra movement to cross obstacles or change elevation; obstacles do not block line of sight.\par\vspace{1.2pt}
    \kw{Shield (X):}\ Put X shield counters on the frame. One counter absorbs an entire attack (all zones), then is removed.\par\vspace{1.2pt}
    \kw{Deathstrike:}\ Continues fighting until end of the turn when destroyed; survives if healed before turn cleanup.\par
    }
  };

  \fill[black, even odd rule] (cardbleed.south west) rectangle (cardbleed.north east) (cardbg.south west) rectangle (cardbg.north east);
\end{tikzpicture}
"""


def create_helpcards(out_dir: str = "build/card/") -> list[str]:
    """Write the helpcard .tex files to out_dir. Returns list of created file paths."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    cards = {
        "Helpcard_Symbols.tex": build_symbols_card(),
        "Helpcard_Keywords.tex": build_keywords_card(),
    }

    created = []
    for filename, content in cards.items():
        filepath = out_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        created.append(str(filepath))

    return created


if __name__ == "__main__":
    written = create_helpcards()
    print(f"Generated {len(written)} helpcard(s): {', '.join(written)}")
