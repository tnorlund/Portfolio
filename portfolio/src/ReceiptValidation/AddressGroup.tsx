import React from 'react';
import { ReceiptWord, ReceiptWordTag } from '../interfaces';
import WordItem from './WordItem';

interface AddressGroupProps {
  words: ReceiptWord[];
  tag: ReceiptWordTag;
  selectedWord: ReceiptWord | null;
  onWordSelect: (word: ReceiptWord) => void;
  groupIndex: number;
  openTagMenu: { groupIndex: number; wordIndex: number; } | null;
  setOpenTagMenu: (value: { groupIndex: number; wordIndex: number; } | null) => void;
  menuRef: React.RefObject<HTMLDivElement>;
}

const AddressGroup: React.FC<AddressGroupProps> = ({
  words,
  tag,
  selectedWord,
  onWordSelect,
  groupIndex,
  openTagMenu,
  setOpenTagMenu,
  menuRef,
}) => {
  return (
    <div style={{
      padding: '8px',
      backgroundColor: 'var(--background-color)',
      border: '1px solid var(--text-color)',
      borderRadius: '4px',
      display: 'flex',
      flexDirection: 'column',
      gap: '4px'
    }}>
      {words.map((word, wordIdx) => (
        <WordItem
          key={wordIdx}
          word={word}
          tag={tag}
          isSelected={selectedWord?.word_id === word.word_id && 
                     selectedWord?.line_id === word.line_id && 
                     selectedWord?.receipt_id === word.receipt_id && 
                     selectedWord?.image_id === word.image_id}
          onWordClick={() => onWordSelect(word)}
          onTagClick={() => {
            setOpenTagMenu(
              openTagMenu?.groupIndex === groupIndex && 
              openTagMenu.wordIndex === wordIdx ? null : 
              { groupIndex, wordIndex: wordIdx }
            );
          }}
          openTagMenu={openTagMenu?.groupIndex === groupIndex && 
                      openTagMenu.wordIndex === wordIdx}
          menuRef={menuRef}
        />
      ))}
    </div>
  );
};

export default AddressGroup; 