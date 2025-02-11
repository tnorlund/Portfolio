import React from "react";
import { ReceiptDetail, ReceiptWord, ReceiptWordTag } from "../interfaces";
import ReceiptBoundingBox from "../ReceiptBoundingBox";
import AddressGroup from "./AddressGroup";

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

  const onWordSelect = React.useCallback((word: ReceiptWord) => {
    setSelectedWord(word);
  }, []);

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

    // Create a group for each tagged word
    return sortedWords
      .map(word => {
        const matchingTag = addressTags.find((tag) => 
          tag.word_id === word.word_id &&
          tag.line_id === word.line_id &&
          tag.receipt_id === word.receipt_id &&
          tag.image_id === word.image_id
        );
        
        return matchingTag ? { words: [word], tag: matchingTag } : null;
      })
      .filter((group): group is GroupedWords => group !== null);
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
          <AddressGroup
            key={index}
            words={group.words}
            tag={group.tag}
            selectedWord={selectedWord}
            onWordSelect={onWordSelect}
            groupIndex={index}
            openTagMenu={openTagMenu}
            setOpenTagMenu={setOpenTagMenu}
            menuRef={menuRef}
          />
        ))}
      </div>
    );
  };

  const handleBoundingBoxClick = (word: ReceiptWord) => {
    if (isAddingTag) {
      setSelectedWord(word);
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
