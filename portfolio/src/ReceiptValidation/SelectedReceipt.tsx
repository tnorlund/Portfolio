import React from "react";
import { ReceiptDetail, ReceiptWord, ReceiptWordTag } from "../interfaces";
import ReceiptBoundingBox from "../ReceiptBoundingBox";

interface WordWithTag extends ReceiptWord {
  tag?: ReceiptWordTag;
}

interface GroupedWords {
  words: ReceiptWord[];
  tag: ReceiptWordTag;
}

interface SelectedReceiptProps {
  selectedReceipt: string | null;
  receiptDetails: { [key: string]: ReceiptDetail };
  cdn_base_url: string;
}

const selectableTags = [
  "line_item_name",
  "address",
  "line_item_price",
  "store_name",
  "date",
  "time",
  "total_amount",
  "phone_number",
  "taxes",
];

const SelectedReceipt: React.FC<SelectedReceiptProps> = ({
  selectedReceipt,
  receiptDetails,
  cdn_base_url,
}) => {
  const [selectedWord, setSelectedWord] = React.useState<ReceiptWord | null>(null);
  const [openTagMenu, setOpenTagMenu] = React.useState<{
    groupIndex: number;
    wordIndex: number;
  } | null>(null);
  const [isAddingTag, setIsAddingTag] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenTagMenu(null);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getAddressGroups = (detail: ReceiptDetail): GroupedWords[] => {
    // Get all address tags
    const addressTags = detail.word_tags.filter((tag) => {
      return tag.tag.trim().toLowerCase() === "address";
    });

    // Sort words by their position (top to bottom, left to right)
    const sortedWords = [...detail.words].sort((a, b) => {
      if (Math.abs(a.bounding_box.y - b.bounding_box.y) < 10) {
        return a.bounding_box.x - b.bounding_box.x;
      }
      return a.bounding_box.y - b.bounding_box.y;
    });
    const groups: GroupedWords[] = [];
    let currentGroup: GroupedWords | null = null;

    sortedWords.forEach((word) => {
      const matchingTag = addressTags.find((tag) => {
        return (
          tag.word_id === word.word_id &&
          tag.line_id === word.line_id &&
          tag.receipt_id === word.receipt_id &&
          tag.image_id === word.image_id
        );
      });

      if (matchingTag) {
        if (!currentGroup) {
          currentGroup = { words: [word], tag: matchingTag };
          groups.push(currentGroup);
        } else {
          // Check if this word is close to the last word in current group
          const lastWord = currentGroup.words[currentGroup.words.length - 1];
          const isNearby =
            Math.abs(word.bounding_box.y - lastWord.bounding_box.y) < 20 ||
            Math.abs(
              word.bounding_box.x -
                (lastWord.bounding_box.x + lastWord.bounding_box.width)
            ) < 30;

          if (isNearby) {
            currentGroup.words.push(word);
          } else {
            currentGroup = { words: [word], tag: matchingTag };
            groups.push(currentGroup);
          }
        }
      } else {
        currentGroup = null;
      }
    });

    return groups;
  };

  const renderStars = (confidence: number | null) => {
    if (confidence === null) return null;
    const stars = '★'.repeat(confidence) + '☆'.repeat(5 - confidence);
    return (
      <span style={{ 
        color: 'var(--text-color)', // Amber-400 for gold stars
        marginLeft: '8px',
        fontSize: '0.875rem'
      }}>
        {stars}
      </span>
    );
  };

  const renderHumanValidation = (validated: boolean | null) => {
    if (validated === null) {
      return (
        <span style={{ 
          width: '16px',
          height: '16px',
          borderRadius: '4px',
          border: '2px solid var(--text-color)',
          display: 'inline-block'
        }} />
      );
    }
    return (
      <span style={{ 
        color: validated ? '#4ade80' : '#ef4444',
        fontSize: '1rem'
      }}>
        {validated ? '✓' : '✗'}
      </span>
    );
  };

  const renderRightPanel = () => {
    if (!selectedReceipt || !receiptDetails[selectedReceipt]) return null;

    const addressGroups = getAddressGroups(receiptDetails[selectedReceipt]);

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between',
          marginBottom: '0.5rem',
          alignItems: 'center'
        }}>
          <div style={{ fontSize: '2rem', color: 'white', fontWeight: 'bold'}}>
            Address
          </div>
          <div style={{
            width: '32px',
            height: '32px',
            borderRadius: '50%',
            backgroundColor: isAddingTag ? 'var(--background-color)' : 'var(--text-color)',
            border: isAddingTag ? '2px solid var(--text-color)' : 'none',
            color: isAddingTag ? 'var(--text-color)' : 'var(--background-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            fontSize: '1.5rem',
            lineHeight: '1',
            paddingBottom: '3px',
            fontWeight: 'bold'
          }} onClick={() => setIsAddingTag(!isAddingTag)}>
            +
          </div>
        </div>
        {addressGroups.map((group, index) => (
          <div key={index} 
               style={{
                 padding: '8px',
                 backgroundColor: 'var(--background-color)',
                 border: '1px solid #e2e8f0',
                 borderRadius: '4px',
                 display: 'flex',
                 flexDirection: 'column',
                 gap: '4px'
               }}>
            {group.words.map((word, wordIdx) => (
              <div
                key={wordIdx}
                style={{
                  cursor: 'pointer',
                  padding: '4px',
                  backgroundColor: selectedWord && 
                    selectedWord.word_id === word.word_id &&
                    selectedWord.line_id === word.line_id &&
                    selectedWord.receipt_id === word.receipt_id &&
                    selectedWord.image_id === word.image_id
                      ? '#1e40af' 
                      : 'transparent',
                  borderRadius: '2px',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  position: 'relative'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {renderHumanValidation(group.tag.human_validated)}
                  <span 
                    style={{ color: 'var(--text-color)' }}
                    onClick={() => setSelectedWord(word)}
                  >
                    {word.text}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {renderStars(group.tag.gpt_confidence)}
                  <span 
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenTagMenu(openTagMenu?.groupIndex === index && 
                        openTagMenu.wordIndex === wordIdx ? null : 
                        { groupIndex: index, wordIndex: wordIdx });
                    }}
                    style={{ 
                      color: group.tag.validated ? '#4ade80' : '#ef4444',
                      backgroundColor: group.tag.validated ? '#065f46' : '#7f1d1d',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      fontSize: '0.875rem',
                      cursor: 'pointer'
                    }}
                  >
                    Address
                  </span>
                </div>
                {openTagMenu?.groupIndex === index && 
                 openTagMenu.wordIndex === wordIdx && (
                  <div ref={menuRef} style={{
                    position: 'absolute',
                    right: 0,
                    top: '100%',
                    backgroundColor: 'var(--background-color)',
                    border: '1px solid var(--text-color)',
                    borderRadius: '4px',
                    zIndex: 10,
                    marginTop: '4px',
                    boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)'
                  }}>
                    {selectableTags.map(tag => (
                      <div
                        key={tag}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenTagMenu(null);
                        }}
                        className="hover:bg-gray-700"
                        style={{
                          padding: '8px 16px',
                          color: 'white',
                          cursor: 'pointer',
                          whiteSpace: 'nowrap'
                        }}
                      >
                        {tag}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    );
  };

  const handleBoundingBoxClick = (word: ReceiptWord) => {
    if (isAddingTag) {
      console.log('Selected word for new tag:', word);
      setIsAddingTag(false);
    }
  };

  return (
    <div className="w-full flex flex-col">
      <h1 className="text-2xl font-bold p-4">Receipt Validation</h1>

      {/* Main container */}
      <div style={{ display: "flex", position: "relative" }}>
        {/* Left panel - Will determine height */}
        <div
          style={{
            width: "50%",
            backgroundColor: "red",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          {selectedReceipt && receiptDetails[selectedReceipt] ? (
            <ReceiptBoundingBox
              detail={receiptDetails[selectedReceipt]}
              width={450}
              isSelected={true}
              cdn_base_url={cdn_base_url}
              highlightedWords={selectedWord ? [selectedWord] : []}
              onClick={isAddingTag ? handleBoundingBoxClick : undefined}
              isAddingTag={isAddingTag}
            />
          ) : (
            <div>Select a receipt</div>
          )}
        </div>

        {/* Right panel - Should scroll */}
        <div
          style={{
            width: "50%",
            backgroundColor: "blue",
            position: "absolute",
            right: 0,
            top: 0,
            bottom: 0,
            overflowY: "auto",
          }}
        >
          <div style={{ padding: "20px" }}>
            {renderRightPanel()}
          </div>
        </div>
      </div>
    </div>
  );
};

export default SelectedReceipt;
